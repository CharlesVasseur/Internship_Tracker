"""Apply adapter check results to firms and opportunities."""

from datetime import datetime
import hashlib
import sqlite3
from urllib.parse import urlsplit

from tracker.adapters.base import AdapterResult, Listing, validate_result
from tracker.importer import normalize


CLOSE_AFTER_MISSES = 3


def make_identity(listing: Listing) -> str:
    """Return a stable source id or normalized listing fingerprint."""
    if listing.source_job_id is not None:
        source_id = listing.source_job_id.strip()
        if source_id:
            return "id:" + source_id

    url = listing.url
    url_path = ""
    if url is not None:
        url_path = urlsplit(url).path.lower().rstrip("/")

    title = normalize(listing.title)
    location = normalize(listing.location_raw) if listing.location_raw is not None else ""
    fingerprint_text = "|".join((title, location, url_path))
    return "fp:" + hashlib.sha1(fingerprint_text.encode("utf-8")).hexdigest()[:16]


def _nullable_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _evidence_text(evidence: tuple[str, ...]) -> str | None:
    if not evidence:
        return None
    text = " | ".join(str(item) for item in evidence)
    return text[:500] if text else None


def apply_check_result(
    conn: sqlite3.Connection,
    firm_id: int,
    result: AdapterResult,
    *,
    started_at: str,
    finished_at: str,
) -> dict:
    """Store a check and atomically update this firm's opportunities."""
    try:
        started = datetime.fromisoformat(started_at)
        finished = datetime.fromisoformat(finished_at)
    except (TypeError, ValueError) as error:
        raise ValueError("started_at and finished_at must be ISO datetime strings") from error
    if finished < started:
        raise ValueError("finished_at must be at or after started_at")

    problems = validate_result(result)
    if problems:
        effective_status = "failed"
        effective_error = "invalid adapter result: " + "; ".join(problems)
        effective_listings: tuple[Listing, ...] = ()
        effective_evidence: tuple[str, ...] = ()
        effective_http_status = None
    else:
        effective_status = result.status
        effective_error = result.error
        effective_listings = result.listings
        effective_evidence = result.evidence
        effective_http_status = result.http_status

    evidence_snippet = _evidence_text(effective_evidence)
    n_results = len(effective_listings) if effective_status in ("ok", "empty") else None

    counts = {
        "status": effective_status,
        "new": 0,
        "changed": 0,
        "unchanged": 0,
        "missing": 0,
        "closed": 0,
        "reopened": 0,
        "duplicates": 0,
        "problems": problems,
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
            INSERT INTO checks (
                firm_id, started_at, finished_at, result, http_status,
                n_results, error, evidence_snippet
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                firm_id,
                started_at,
                finished_at,
                effective_status,
                effective_http_status,
                n_results,
                effective_error,
                evidence_snippet,
            ),
        )

        if effective_status in ("ok", "empty"):
            conn.execute(
                """
                UPDATE firms
                SET last_check_status = ?, last_successful_check = ?
                WHERE id = ?
                """,
                (effective_status, finished_at, firm_id),
            )
        else:
            conn.execute(
                "UPDATE firms SET last_check_status = ? WHERE id = ?",
                (effective_status, firm_id),
            )

        if effective_status not in ("ok", "empty"):
            return counts

        unique_listings: dict[str, Listing] = {}
        for listing in effective_listings:
            identity = make_identity(listing)
            if identity in unique_listings:
                counts["duplicates"] += 1
            else:
                unique_listings[identity] = listing

        existing_rows = conn.execute(
            """
            SELECT id, fingerprint, title, location, url, description_text,
                   missing_count, state
            FROM opportunities
            WHERE firm_id = ?
              AND (fingerprint IS NULL OR fingerprint NOT LIKE 'manual:%')
            """,
            (firm_id,),
        ).fetchall()
        existing = {
            row["fingerprint"]: row
            for row in existing_rows
            if row["fingerprint"] is not None
        }

        for identity, listing in unique_listings.items():
            title = _nullable_text(listing.title)
            location = _nullable_text(listing.location_raw)
            url = _nullable_text(listing.url)
            description = _nullable_text(listing.description)
            language = _nullable_text(listing.language)
            source_job_id = _nullable_text(listing.source_job_id)

            if identity not in existing:
                conn.execute(
                    """
                    INSERT INTO opportunities (
                        firm_id, source_job_id, title, location, url,
                        description_text, language, first_seen, last_seen,
                        missing_count, state, fingerprint, evidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'Needs review', ?, ?)
                    """,
                    (
                        firm_id,
                        source_job_id,
                        title,
                        location,
                        url,
                        description,
                        language,
                        finished_at,
                        finished_at,
                        identity,
                        evidence_snippet,
                    ),
                )
                counts["new"] += 1
                continue

            row = existing[identity]
            changed = (
                _nullable_text(row["title"]) != title
                or _nullable_text(row["location"]) != location
                or _nullable_text(row["url"]) != url
                or _nullable_text(row["description_text"]) != description
            )

            reopened = row["state"] == "Closed"
            conn.execute(
                """
                UPDATE opportunities
                SET title = ?, location = ?, url = ?, description_text = ?,
                    last_seen = ?, missing_count = 0,
                    state = CASE WHEN state = 'Closed' THEN 'Needs review' ELSE state END
                WHERE id = ?
                """,
                (title, location, url, description, finished_at, row["id"]),
            )

            if changed:
                counts["changed"] += 1
            else:
                counts["unchanged"] += 1
            if reopened:
                counts["reopened"] += 1

        seen_identities = set(unique_listings)
        for identity, row in existing.items():
            if identity in seen_identities:
                continue
            missing_count = (row["missing_count"] or 0) + 1
            counts["missing"] += 1

            closed = missing_count >= CLOSE_AFTER_MISSES and row["state"] != "Closed"
            new_state = "Closed" if closed else row["state"]
            conn.execute(
                """
                UPDATE opportunities
                SET missing_count = ?, state = ?
                WHERE id = ?
                """,
                (missing_count, new_state, row["id"]),
            )
            if closed:
                counts["closed"] += 1

    return counts
