"""Write services for application and activity records."""

from datetime import date
import sqlite3

from tracker.models import (
    ACTIVITY_CREATED,
    ACTIVITY_NOTE,
    ACTIVITY_STAGE_CHANGE,
    APPLICATION_STAGES,
)


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
    next_action_date: str | None,
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
    if not isinstance(url, str):
        raise ValueError("URL must be a non-empty HTTP or HTTPS URL")
    clean_url = url.strip()
    if not clean_url:
        raise ValueError("URL must not be empty")
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
