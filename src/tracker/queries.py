"""Read-only SQLite queries for the tracker UI."""

from __future__ import annotations

from datetime import date, timedelta
import os
import sqlite3

from tracker.models import APPLICATION_STAGES, MANUAL_PREFIX


def get_db_path() -> str:
    """Return the configured tracker database path."""
    return os.environ.get("TRACKER_DB", "data/tracker.db")


def list_firms(
    conn: sqlite3.Connection,
    countries: list[str] | None = None,
    priorities: list[str] | None = None,
    stages: list[str] | None = None,
    search: str | None = None,
) -> list[dict]:
    """Return firms and their latest application fields."""
    sql = """
        SELECT
            f.id AS firm_id,
            f.name,
            f.employer_key,
            f.country,
            f.city,
            f.priority,
            f.category,
            f.actuarial_depth,
            latest.stage AS stage,
            latest.next_action_date,
            f.opening_window,
            f.last_verified,
            f.careers_url,
            f.source_type,
            f.last_successful_check,
            f.last_check_status,
            (
                SELECT COUNT(*)
                FROM applications AS app
                WHERE app.firm_id = f.id
            ) AS n_applications
        FROM firms AS f
        LEFT JOIN applications AS latest
            ON latest.id = (
                SELECT MAX(a.id)
                FROM applications AS a
                WHERE a.firm_id = f.id
            )
        WHERE 1 = 1
    """
    parameters: list[object] = []

    if countries:
        placeholders = ", ".join("?" for _ in countries)
        sql += f" AND f.country IN ({placeholders})"
        parameters.extend(countries)
    if priorities:
        placeholders = ", ".join("?" for _ in priorities)
        sql += f" AND f.priority IN ({placeholders})"
        parameters.extend(priorities)
    if stages:
        placeholders = ", ".join("?" for _ in stages)
        sql += (
            f" AND EXISTS ("
            f"SELECT 1 FROM applications AS stage_app "
            f"WHERE stage_app.firm_id = f.id "
            f"AND stage_app.stage IN ({placeholders}))"
        )
        parameters.extend(stages)
    if search:
        sql += (
            " AND (LOWER(COALESCE(f.name, '')) LIKE ? "
            "OR LOWER(COALESCE(f.employer_key, '')) LIKE ?)"
        )
        needle = f"%{search.casefold()}%"
        parameters.extend((needle, needle))

    sql += """
        ORDER BY
            CASE f.priority
                WHEN 'P1' THEN 1
                WHEN 'P2' THEN 2
                WHEN 'P3' THEN 3
                WHEN 'P4' THEN 4
                ELSE 5
            END,
            f.name,
            f.country
    """
    rows = conn.execute(sql, parameters).fetchall()
    return [dict(row) for row in rows]


def get_filter_options(conn: sqlite3.Connection) -> dict:
    """Return sorted values available for the sidebar filters."""
    countries = [
        row["country"]
        for row in conn.execute(
            "SELECT DISTINCT country FROM firms "
            "WHERE country IS NOT NULL ORDER BY country"
        )
    ]
    existing_priorities = {
        row["priority"]
        for row in conn.execute(
            "SELECT DISTINCT priority FROM firms "
            "WHERE priority IS NOT NULL"
        )
    }
    priorities = [priority for priority in ("P1", "P2", "P3", "P4") if priority in existing_priorities]

    stages = list(APPLICATION_STAGES)

    return {
        "countries": countries,
        "priorities": priorities,
        "stages": stages,
    }


def count_by_stage(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Return application counts for every stage in pipeline order."""
    counts = {
        row["stage"]: row["count"]
        for row in conn.execute(
            """
            SELECT stage, COUNT(*) AS count
            FROM applications
            GROUP BY stage
            """
        )
    }
    return [(stage, counts.get(stage, 0)) for stage in APPLICATION_STAGES]


def list_pipeline(
    conn: sqlite3.Connection,
    stages: list[str],
) -> list[dict]:
    """Return applications in selected stages, ordered for pipeline review."""
    if not stages:
        return []

    placeholders = ", ".join("?" for _ in stages)
    stage_order = " ".join(
        f"WHEN '{stage}' THEN {index}"
        for index, stage in enumerate(APPLICATION_STAGES)
    )
    sql = f"""
        SELECT
            app.id AS application_id,
            f.id AS firm_id,
            f.name AS firm_name,
            f.country,
            f.city,
            f.priority,
            app.stage,
            app.next_action,
            app.next_action_date,
            app.submitted_date
        FROM applications AS app
        JOIN firms AS f ON f.id = app.firm_id
        WHERE app.stage IN ({placeholders})
        ORDER BY
            CASE app.stage
                {stage_order}
                ELSE {len(APPLICATION_STAGES)}
            END,
            CASE WHEN app.next_action_date IS NULL THEN 1 ELSE 0 END,
            app.next_action_date,
            f.name
    """
    rows = conn.execute(sql, stages).fetchall()
    return [dict(row) for row in rows]


def get_firm(conn: sqlite3.Connection, firm_id: int) -> dict | None:
    """Return every column of one firm, or None when it does not exist."""
    row = conn.execute(
        "SELECT * FROM firms WHERE id = ?",
        (firm_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def list_applications(
    conn: sqlite3.Connection,
    firm_id: int,
) -> list[dict]:
    """Return every application for a firm, newest application first."""
    rows = conn.execute(
        """
        SELECT id, stage, submitted_date, next_action, next_action_date,
               priority_override, links, notes, opportunity_id
        FROM applications
        WHERE firm_id = ?
        ORDER BY id DESC
        """,
        (firm_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def list_next_actions(
    conn: sqlite3.Connection,
    today: str,
    horizon_days: int = 7,
) -> dict:
    """Return applications with overdue, due, unscheduled or unreadable next actions."""
    today_date = date.fromisoformat(today)
    horizon_date = today_date + timedelta(days=horizon_days)

    rows = conn.execute(
        """
        SELECT
            app.id AS application_id,
            f.id AS firm_id,
            f.name AS firm_name,
            f.country,
            f.city,
            app.stage,
            app.next_action,
            app.next_action_date
        FROM applications AS app
        JOIN firms AS f ON f.id = app.firm_id
        WHERE app.stage != 'Rejected'
        """
    ).fetchall()

    overdue: list[dict] = []
    today_items: list[dict] = []
    upcoming: list[dict] = []
    no_date: list[dict] = []
    unreadable: list[dict] = []

    for row in rows:
        item = dict(row)
        value = item["next_action_date"]
        if value is not None:
            try:
                action_date = date.fromisoformat(value)
            except (TypeError, ValueError):
                unreadable.append(item)
                continue

            if action_date < today_date:
                overdue.append(item)
            elif action_date == today_date:
                today_items.append(item)
            elif action_date <= horizon_date:
                upcoming.append(item)
            continue

        next_action = item["next_action"]
        if isinstance(next_action, str) and next_action.strip():
            no_date.append(item)

    overdue.sort(
        key=lambda item: (item["next_action_date"], item["firm_name"])
    )
    today_items.sort(key=lambda item: item["firm_name"])
    upcoming.sort(
        key=lambda item: (item["next_action_date"], item["firm_name"])
    )
    no_date.sort(key=lambda item: item["firm_name"])
    unreadable.sort(key=lambda item: item["firm_name"])

    return {
        "overdue": overdue,
        "today": today_items,
        "upcoming": upcoming,
        "no_date": no_date,
        "unreadable": unreadable,
    }


def list_opportunities(
    conn: sqlite3.Connection,
    firm_id: int,
) -> list[dict]:
    """Return opportunities for a firm with recent observations first."""
    rows = conn.execute(
        """
        SELECT id, title, location, url, deadline, state, first_seen, last_seen,
               missing_count, is_internship, fingerprint, description_text
        FROM opportunities
        WHERE firm_id = ?
        ORDER BY
            CASE WHEN last_seen IS NULL THEN 1 ELSE 0 END,
            last_seen DESC
        """,
        (firm_id,),
    ).fetchall()
    opportunities = []
    for row in rows:
        opportunity = dict(row)
        fingerprint = opportunity.pop("fingerprint")
        opportunity["is_manual"] = (
            isinstance(fingerprint, str)
            and fingerprint.startswith(MANUAL_PREFIX)
        )
        opportunities.append(opportunity)
    return opportunities


def list_opportunity_deadlines(
    conn: sqlite3.Connection,
    today: str,
    horizon_days: int = 14,
) -> dict:
    """Return active opportunity deadlines grouped by soon, passed, or unreadable."""
    try:
        today_date = date.fromisoformat(today)
    except (TypeError, ValueError) as error:
        raise ValueError("today must be a valid ISO date") from error
    horizon_date = today_date + timedelta(days=horizon_days)

    rows = conn.execute(
        """
        SELECT
            o.id AS opportunity_id,
            f.id AS firm_id,
            f.name AS firm_name,
            f.country,
            f.city,
            o.title,
            o.url,
            o.deadline,
            o.state,
            o.fingerprint
        FROM opportunities AS o
        JOIN firms AS f ON f.id = o.firm_id
        WHERE o.state IN ('Open', 'Upcoming', 'Needs review')
        """
    ).fetchall()

    soon: list[dict] = []
    passed: list[dict] = []
    unreadable: list[dict] = []

    for row in rows:
        item = dict(row)
        fingerprint = item.pop("fingerprint")
        item["is_manual"] = (
            isinstance(fingerprint, str)
            and fingerprint.startswith(MANUAL_PREFIX)
        )
        value = item["deadline"]
        if value is None:
            continue

        try:
            deadline_date = date.fromisoformat(value)
        except (TypeError, ValueError):
            unreadable.append(item)
            continue

        if deadline_date < today_date:
            passed.append(item)
        elif today_date <= deadline_date <= horizon_date:
            soon.append(item)

    soon.sort(
        key=lambda item: (
            date.fromisoformat(item["deadline"]),
            item["firm_name"],
        )
    )
    passed.sort(
        key=lambda item: (
            -date.fromisoformat(item["deadline"]).toordinal(),
            item["firm_name"],
        )
    )
    unreadable.sort(key=lambda item: item["firm_name"])

    return {
        "soon": soon,
        "passed": passed,
        "unreadable": unreadable,
    }


def list_checks(
    conn: sqlite3.Connection,
    firm_id: int,
    limit: int = 10,
) -> list[dict]:
    """Return the newest checks for one firm."""
    rows = conn.execute(
        """
        SELECT id, started_at, finished_at, result, http_status,
               n_results, error, evidence_snippet
        FROM checks
        WHERE firm_id = ?
        ORDER BY finished_at DESC, id DESC
        LIMIT ?
        """,
        (firm_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def list_other_offices(
    conn: sqlite3.Connection,
    firm_id: int,
) -> list[dict]:
    """Return other office rows for the same profile and employer."""
    firm = conn.execute(
        "SELECT profile, employer_key FROM firms WHERE id = ?",
        (firm_id,),
    ).fetchone()
    if firm is None or firm["employer_key"] is None:
        return []

    rows = conn.execute(
        """
        SELECT id AS firm_id, name, country, city
        FROM firms
        WHERE profile = ?
          AND employer_key = ?
          AND id != ?
        ORDER BY country, city
        """,
        (firm["profile"], firm["employer_key"], firm_id),
    ).fetchall()
    return [dict(row) for row in rows]


def list_activities(
    conn: sqlite3.Connection,
    application_id: int,
) -> list[dict]:
    """Return activities for an application, newest first."""
    rows = conn.execute(
        """
        SELECT id, type, date, notes, outcome
        FROM activities
        WHERE application_id = ?
        ORDER BY date DESC, id DESC
        """,
        (application_id,),
    ).fetchall()
    return [dict(row) for row in rows]
