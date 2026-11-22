"""Write services for application, activity, and manual check records."""

from datetime import date, datetime
import json
import sqlite3
from uuid import uuid4
from urllib.parse import urlsplit

from tracker.adapters import ADAPTERS
from tracker.models import (
    ACTIVITY_CREATED,
    ACTIVITY_NOTE,
    ACTIVITY_STAGE_CHANGE,
    APPLICATION_STAGES,
    MANUAL_PREFIX,
    OPPORTUNITY_STATES,
)

MANUAL_CHECK_EVIDENCE = "manual: checked by user"
TERMS_READINGS = ("silent", "permission_granted")


def _nullable_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _parse_date(value: str | date | None, field_name: str = "date") -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as error:
        raise ValueError(f"{field_name} must be YYYY-MM-DD") from error


def _validate_url(url: str | None) -> str | None:
    """Validate an optional HTTP or HTTPS URL and return its trimmed value."""
    if url is None:
        return None
    if not isinstance(url, str):
        raise ValueError("URL must be a non-empty HTTP or HTTPS URL")
    clean_url = url.strip()
    if not clean_url:
        return None
    if len(clean_url) > 2000:
        raise ValueError("URL must be at most 2000 characters")
    if any(character.isspace() for character in clean_url):
        raise ValueError("URL must not contain whitespace")

    lower_url = clean_url.lower()
    if not (lower_url.startswith("http://") or lower_url.startswith("https://")):
        raise ValueError("URL must start with http:// or https://")
    scheme_end = clean_url.find("://") + 3
    if not clean_url[scheme_end:].strip():
        raise ValueError("URL must contain something after the scheme")
    if not urlsplit(clean_url).netloc:
        raise ValueError("URL must contain a host")
    return clean_url


def set_firm_monitoring(
    conn: sqlite3.Connection,
    firm_id: int,
    *,
    adapter: str,
    list_url: str,
    api_url_template: str,
    terms_url: str,
    terms_reading: str,
    terms_reviewed_on: str | date,
    terms_note: str | None = None,
    on_date: str | date | None = None,
) -> None:
    """Enable automated checks after validating their source and terms record."""
    if adapter not in ADAPTERS:
        raise ValueError(f"unknown adapter: {adapter}")
    if not isinstance(api_url_template, str):
        raise ValueError("api_url_template must be a URL template")
    if api_url_template.count("{page}") != 1:
        raise ValueError("api_url_template must contain {page} exactly once")
    replaced_template = api_url_template.replace("{page}", "2")
    if "{" in replaced_template or "}" in replaced_template:
        raise ValueError("api_url_template contains unsupported braces")

    list_value = _validate_url(list_url)
    template_value = _validate_url(replaced_template)
    terms_value = _validate_url(terms_url)
    if list_value is None or template_value is None or terms_value is None:
        raise ValueError("list_url, api_url_template and terms_url are required URLs")

    list_host = urlsplit(list_value).hostname
    template_host = urlsplit(template_value).hostname
    if (
        list_host is None
        or template_host is None
        or list_host.lower() != template_host.lower()
    ):
        raise ValueError("list_url and api_url_template must use the same host")

    if terms_reading == "forbids":
        raise ValueError(
            "automatic checks are not allowed when the terms forbid automated access"
        )
    if terms_reading not in TERMS_READINGS:
        raise ValueError(f"invalid terms_reading: {terms_reading}")

    reviewed = _parse_date(terms_reviewed_on, "terms_reviewed_on")
    if reviewed is None:
        raise ValueError("terms_reviewed_on must be YYYY-MM-DD")
    effective_date = _parse_date(on_date, "on_date")
    if effective_date is None:
        effective_date = date.today().isoformat()
    if reviewed > effective_date:
        raise ValueError("terms_reviewed_on must not be later than on_date")

    source_config = {
        "adapter": adapter,
        "list_url": list_value,
        "api_url_template": api_url_template,
        "first_api_page": 2,
        "max_pages": 30,
        "terms": {
            "url": terms_value,
            "reading": terms_reading,
            "reviewed_on": reviewed,
            "note": _nullable_text(terms_note),
        },
    }

    with conn:
        firm = conn.execute(
            "SELECT id FROM firms WHERE id = ?",
            (firm_id,),
        ).fetchone()
        if firm is None:
            raise LookupError(f"unknown firm id: {firm_id}")
        conn.execute(
            """
            UPDATE firms
            SET monitoring = 'automated', careers_url = ?,
                source_type = 'careers_page', source_config = ?
            WHERE id = ?
            """,
            (list_value, json.dumps(source_config), firm_id),
        )


def disable_firm_monitoring(
    conn: sqlite3.Connection,
    firm_id: int,
) -> None:
    """Disable automatic checks without removing their saved configuration."""
    with conn:
        firm = conn.execute(
            "SELECT id FROM firms WHERE id = ?",
            (firm_id,),
        ).fetchone()
        if firm is None:
            raise LookupError(f"unknown firm id: {firm_id}")
        conn.execute(
            "UPDATE firms SET monitoring = 'manual' WHERE id = ?",
            (firm_id,),
        )


def add_application(
    conn: sqlite3.Connection,
    firm_id: int,
    stage: str = "To research",
    on_date: str | date | None = None,
) -> int:
    """Create an application and its creation activity."""
    if stage not in APPLICATION_STAGES:
        raise ValueError(f"invalid application stage: {stage}")
    activity_date = _parse_date(on_date, "on_date")
    if activity_date is None:
        activity_date = date.today().isoformat()

    with conn:
        firm = conn.execute(
            "SELECT id FROM firms WHERE id = ?",
            (firm_id,),
        ).fetchone()
        if firm is None:
            raise LookupError(f"unknown firm id: {firm_id}")

        cursor = conn.execute(
            "INSERT INTO applications (firm_id, stage) VALUES (?, ?)",
            (firm_id, stage),
        )
        application_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO activities (application_id, type, date, notes)
            VALUES (?, ?, ?, ?)
            """,
            (
                application_id,
                ACTIVITY_CREATED,
                activity_date,
                f"Created at stage {stage}",
            ),
        )
        return application_id


def save_application(
    conn: sqlite3.Connection,
    application_id: int,
    *,
    stage: str,
    on_date: str | date,
    next_action: str | None,
    next_action_date: str | date | None,
    notes: str | None,
) -> dict:
    """Save application fields and log a stage change when it occurs."""
    if stage not in APPLICATION_STAGES:
        raise ValueError(f"invalid application stage: {stage}")
    activity_date = _parse_date(on_date, "on_date")
    if activity_date is None:
        raise ValueError("on_date must be YYYY-MM-DD")
    canonical_next_action_date = _parse_date(
        next_action_date,
        "next_action_date",
    )
    next_action_value = _nullable_text(next_action)
    notes_value = _nullable_text(notes)

    with conn:
        current = conn.execute(
            """
            SELECT id, stage, submitted_date
            FROM applications
            WHERE id = ?
            """,
            (application_id,),
        ).fetchone()
        if current is None:
            raise LookupError(f"unknown application id: {application_id}")

        old_stage = current["stage"]
        stage_changed = stage != old_stage
        submitted_date = current["submitted_date"]

        if stage_changed:
            if stage == "Applied" and submitted_date is None:
                submitted_date = activity_date
            conn.execute(
                """
                UPDATE applications
                SET stage = ?, next_action = ?, next_action_date = ?,
                    notes = ?, submitted_date = ?
                WHERE id = ?
                """,
                (
                    stage,
                    next_action_value,
                    canonical_next_action_date,
                    notes_value,
                    submitted_date,
                    application_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO activities (application_id, type, date, notes)
                VALUES (?, ?, ?, ?)
                """,
                (
                    application_id,
                    ACTIVITY_STAGE_CHANGE,
                    activity_date,
                    f"{old_stage} -> {stage}",
                ),
            )
        else:
            conn.execute(
                """
                UPDATE applications
                SET next_action = ?, next_action_date = ?, notes = ?
                WHERE id = ?
                """,
                (
                    next_action_value,
                    canonical_next_action_date,
                    notes_value,
                    application_id,
                ),
            )

    return {"stage_changed": stage_changed}


def add_note(
    conn: sqlite3.Connection,
    application_id: int,
    text: str,
    on_date: str | date | None = None,
) -> int:
    """Append a note activity to an application."""
    note_text = text.strip() if isinstance(text, str) else ""
    if not note_text:
        raise ValueError("note text must not be empty")
    activity_date = _parse_date(on_date, "on_date")
    if activity_date is None:
        activity_date = date.today().isoformat()

    with conn:
        application = conn.execute(
            "SELECT id FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        if application is None:
            raise LookupError(
                f"unknown application id: {application_id}"
            )

        cursor = conn.execute(
            """
            INSERT INTO activities (application_id, type, date, notes)
            VALUES (?, ?, ?, ?)
            """,
            (
                application_id,
                ACTIVITY_NOTE,
                activity_date,
                note_text,
            ),
        )
        return cursor.lastrowid


def update_firm_link(
    conn: sqlite3.Connection,
    firm_id: int,
    url: str,
    link_type: str,
    apply_to_other_offices: bool = False,
) -> dict:
    """Save a real firm link without fetching or opening the URL."""
    if isinstance(url, str) and not url.strip():
        raise ValueError("URL must not be empty")
    clean_url = _validate_url(url)
    if clean_url is None:
        raise ValueError("URL must not be empty")
    if link_type not in ("direct_posting", "careers_page"):
        raise ValueError(
            "link_type must be direct_posting or careers_page"
        )

    with conn:
        firm = conn.execute(
            "SELECT id, profile, employer_key FROM firms WHERE id = ?",
            (firm_id,),
        ).fetchone()
        if firm is None:
            raise LookupError(f"unknown firm id: {firm_id}")

        conn.execute(
            """
            UPDATE firms
            SET careers_url = ?, source_type = ?
            WHERE id = ?
            """,
            (clean_url, link_type, firm_id),
        )
        updated = 1

        if apply_to_other_offices:
            employer_key = firm["employer_key"]
            if employer_key is not None:
                cursor = conn.execute(
                    """
                    UPDATE firms
                    SET careers_url = ?, source_type = ?
                    WHERE profile = ?
                      AND employer_key = ?
                      AND id != ?
                      AND (source_type = 'search_link' OR source_type IS NULL)
                    """,
                    (
                        clean_url,
                        link_type,
                        firm["profile"],
                        employer_key,
                        firm_id,
                    ),
                )
                updated += cursor.rowcount

    return {"updated": updated}


def add_manual_opportunity(
    conn: sqlite3.Connection,
    firm_id: int,
    title: str,
    url: str | None = None,
    location: str | None = None,
    deadline: str | date | None = None,
    notes: str | None = None,
    state: str = "Open",
    on_date: str | date | None = None,
) -> int:
    """Add a user-created opportunity without making it check-managed."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must not be empty")
    title_value = title.strip()
    if len(title_value) > 300:
        raise ValueError("title must be at most 300 characters")
    if state not in OPPORTUNITY_STATES:
        raise ValueError(f"invalid opportunity state: {state}")

    url_value = _validate_url(url)
    location_value = _nullable_text(location)
    notes_value = _nullable_text(notes)
    deadline_value = _parse_date(deadline, "deadline")
    on_date_value = _parse_date(on_date, "on_date")
    if on_date_value is None:
        on_date_value = date.today().isoformat()

    with conn:
        firm = conn.execute(
            "SELECT id FROM firms WHERE id = ?",
            (firm_id,),
        ).fetchone()
        if firm is None:
            raise LookupError(f"unknown firm id: {firm_id}")

        cursor = conn.execute(
            """
            INSERT INTO opportunities (
                firm_id, source_job_id, title, location, url,
                description_text, first_seen, last_seen, missing_count,
                state, fingerprint, evidence, deadline
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                firm_id,
                title_value,
                location_value,
                url_value,
                notes_value,
                on_date_value,
                on_date_value,
                state,
                MANUAL_PREFIX + uuid4().hex,
                "manual: added by user",
                deadline_value,
            ),
        )
        return cursor.lastrowid


def update_manual_opportunity(
    conn: sqlite3.Connection,
    opportunity_id: int,
    *,
    title: str,
    url: str | None,
    location: str | None,
    deadline: str | date | None,
    notes: str | None,
    state: str,
) -> None:
    """Update only an opportunity created manually."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must not be empty")
    title_value = title.strip()
    if len(title_value) > 300:
        raise ValueError("title must be at most 300 characters")
    if state not in OPPORTUNITY_STATES:
        raise ValueError(f"invalid opportunity state: {state}")

    url_value = _validate_url(url)
    location_value = _nullable_text(location)
    notes_value = _nullable_text(notes)
    deadline_value = _parse_date(deadline, "deadline")

    with conn:
        opportunity = conn.execute(
            "SELECT id, fingerprint FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        if opportunity is None:
            raise LookupError(f"unknown opportunity id: {opportunity_id}")
        fingerprint = opportunity["fingerprint"]
        if not (
            isinstance(fingerprint, str)
            and fingerprint.startswith(MANUAL_PREFIX)
        ):
            raise ValueError("only manual opportunities can be edited")

        conn.execute(
            """
            UPDATE opportunities
            SET title = ?, location = ?, url = ?, deadline = ?,
                description_text = ?, state = ?
            WHERE id = ?
            """,
            (
                title_value,
                location_value,
                url_value,
                deadline_value,
                notes_value,
                state,
                opportunity_id,
            ),
        )


def set_opportunity_state(
    conn: sqlite3.Connection,
    opportunity_id: int,
    state: str,
) -> None:
    """Set one opportunity's state without changing any other fields."""
    if state not in OPPORTUNITY_STATES:
        raise ValueError(f"invalid opportunity state: {state}")

    with conn:
        opportunity = conn.execute(
            "SELECT id FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        if opportunity is None:
            raise LookupError(f"unknown opportunity id: {opportunity_id}")

        conn.execute(
            "UPDATE opportunities SET state = ? WHERE id = ?",
            (state, opportunity_id),
        )


def delete_manual_opportunity(
    conn: sqlite3.Connection,
    opportunity_id: int,
) -> None:
    """Delete a manual opportunity if no application references it."""
    with conn:
        opportunity = conn.execute(
            "SELECT id, fingerprint FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        if opportunity is None:
            raise LookupError(f"unknown opportunity id: {opportunity_id}")
        fingerprint = opportunity["fingerprint"]
        if not (
            isinstance(fingerprint, str)
            and fingerprint.startswith(MANUAL_PREFIX)
        ):
            raise ValueError("only manual opportunities can be deleted")

        application_count = conn.execute(
            "SELECT COUNT(*) AS count FROM applications "
            "WHERE opportunity_id = ?",
            (opportunity_id,),
        ).fetchone()["count"]
        if application_count:
            raise ValueError(
                f"{application_count} application"
                f"{'s' if application_count != 1 else ''} reference"
                f"{'s' if application_count == 1 else ''} "
                "this manual opportunity"
            )

        conn.execute(
            "DELETE FROM opportunities WHERE id = ?",
            (opportunity_id,),
        )


def set_application_opportunity(
    conn: sqlite3.Connection,
    application_id: int,
    opportunity_id: int | None,
) -> None:
    """Link or clear an application-opportunity relationship."""
    with conn:
        application = conn.execute(
            "SELECT id, firm_id FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        if application is None:
            raise LookupError(f"unknown application id: {application_id}")

        if opportunity_id is None:
            conn.execute(
                "UPDATE applications SET opportunity_id = NULL WHERE id = ?",
                (application_id,),
            )
            return

        opportunity = conn.execute(
            "SELECT id, firm_id FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        if opportunity is None:
            raise LookupError(f"unknown opportunity id: {opportunity_id}")
        if opportunity["firm_id"] != application["firm_id"]:
            raise ValueError(
                "opportunity belongs to a different firm"
            )

        conn.execute(
            "UPDATE applications SET opportunity_id = ? WHERE id = ?",
            (opportunity_id, application_id),
        )


def record_manual_check(
    conn: sqlite3.Connection,
    firm_id: int,
    checked_at: str,
) -> int:
    """Record a user's manual check without changing opportunities."""
    try:
        datetime.fromisoformat(checked_at)
    except (TypeError, ValueError) as error:
        raise ValueError("checked_at must be an ISO datetime string") from error

    with conn:
        firm = conn.execute(
            "SELECT id FROM firms WHERE id = ?",
            (firm_id,),
        ).fetchone()
        if firm is None:
            raise LookupError(f"unknown firm id: {firm_id}")

        cursor = conn.execute(
            """
            INSERT INTO checks (
                firm_id, started_at, finished_at, result, http_status,
                n_results, error, evidence_snippet
            ) VALUES (?, ?, ?, 'ok', NULL, NULL, NULL, ?)
            """,
            (firm_id, checked_at, checked_at, MANUAL_CHECK_EVIDENCE),
        )
        conn.execute(
            """
            UPDATE firms
            SET last_check_status = 'ok', last_successful_check = ?
            WHERE id = ?
            """,
            (checked_at, firm_id),
        )
        return cursor.lastrowid
