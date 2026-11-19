"""Focused tests for database initialisation and schema constraints."""

import sqlite3

import pytest

from tracker.db import connect, init_db


def test_initialises_in_temporary_database(tmp_path):
    database_path = tmp_path / "tracker.db"

    init_db(database_path)
    init_db(database_path)
    assert database_path.exists()


def test_creates_all_tables(tmp_path):
    database_path = tmp_path / "tracker.db"
    init_db(database_path)

    with connect(database_path) as connection:
        table_names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    assert table_names == {
        "firms",
        "opportunities",
        "applications",
        "activities",
        "checks",
        "point_events",
    }


def test_rejects_unknown_application_stage(tmp_path):
    database_path = tmp_path / "tracker.db"
    init_db(database_path)

    with connect(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO applications (firm_id, stage) VALUES (?, ?)",
                (1, "Unknown"),
            )


def test_rejects_duplicate_point_event_key(tmp_path):
    database_path = tmp_path / "tracker.db"
    init_db(database_path)

    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO point_events (idempotency_key) VALUES (?)",
            ("event-1",),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO point_events (idempotency_key) VALUES (?)",
                ("event-1",),
            )
