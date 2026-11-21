import sqlite3

import pytest

from tracker.db import SCHEMA_VERSION, connect, init_db


def opportunity_columns(connection):
    return [row["name"] for row in connection.execute(
        "PRAGMA table_info(opportunities)"
    )]


def test_fresh_init_sets_schema_version_two_and_deadline(tmp_path):
    database_path = tmp_path / "tracker.db"
    init_db(database_path)

    with connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert SCHEMA_VERSION == 2
        assert "deadline" in opportunity_columns(connection)


def test_old_database_is_migrated_without_losing_rows(tmp_path):
    database_path = tmp_path / "tracker.db"
    init_db(database_path)
    with connect(database_path) as connection:
        connection.execute(
            "ALTER TABLE opportunities DROP COLUMN deadline"
        )
        connection.execute("PRAGMA user_version = 0")
        connection.execute(
            "INSERT INTO firms (profile, name, country) "
            "VALUES (?, ?, ?)",
            ("profile", "Example Firm", "France"),
        )
        firm_id = connection.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO opportunities (firm_id, title) VALUES (?, ?)",
            (firm_id, "Saved role"),
        )

    init_db(database_path)

    with connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "deadline" in opportunity_columns(connection)
        row = connection.execute(
            "SELECT title FROM opportunities"
        ).fetchone()
        assert row["title"] == "Saved role"

    init_db(database_path)


def test_newer_database_is_refused_without_changing_version(tmp_path):
    database_path = tmp_path / "tracker.db"
    init_db(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(
        RuntimeError,
        match="database schema version 99 is newer than this program supports \\(2\\)",
    ):
        init_db(database_path)

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
