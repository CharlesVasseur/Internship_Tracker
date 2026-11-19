"""Read-only SQLite queries for the tracker UI."""

from __future__ import annotations

import os
import sqlite3

from tracker.models import APPLICATION_STAGES


def get_db_path() -> str:
    """Return the configured tracker database path."""
    return os.environ.get("TRACKER_DB", "data/charles.db")


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
        sql += f" AND latest.stage IN ({placeholders})"
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


def list_opportunities(
    conn: sqlite3.Connection,
    firm_id: int,
) -> list[dict]:
    """Return opportunities for a firm with recent observations first."""
    rows = conn.execute(
        """
        SELECT id, title, location, url, state, first_seen, last_seen,
               is_internship
        FROM opportunities
        WHERE firm_id = ?
        ORDER BY
            CASE WHEN last_seen IS NULL THEN 1 ELSE 0 END,
            last_seen DESC
        """,
        (firm_id,),
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
