"""Offline tests for application write services."""

import sqlite3

import pytest

from tracker.db import connect, init_db
from tracker.service import add_application, add_note, save_application


def seed_database(tmp_path):
    database_path = tmp_path / "tracker.db"
    init_db(database_path)
    connection = connect(database_path)
    connection.execute(
        """
        INSERT INTO firms (profile, name, employer_key, country, city)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("profile", "Example Firm", "example firm", "France", "Paris"),
    )
    firm_a = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    connection.execute(
        """
        INSERT INTO firms (profile, name, employer_key, country, city)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("profile", "Sample Re", "sample re", "Switzerland", "Zurich"),
    )
    firm_b = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    application_a = add_application(
        connection,
        firm_a,
        stage="To research",
        on_date="2026-11-19",
    )
    application_b = add_application(
        connection,
        firm_b,
        stage="To research",
        on_date="2026-11-19",
    )
    connection.commit()
    return connection, firm_a, firm_b, application_a, application_b


def test_stage_change_writes_one_activity(tmp_path):
    connection, _, _, application_id, _ = seed_database(tmp_path)
    try:
        result = save_application(
            connection,
            application_id,
            stage="Contacted",
            on_date="2026-11-20",
            next_action="Email",
            next_action_date="2026-11-21",
            notes="",
        )
        row = connection.execute(
            "SELECT stage, next_action FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        activities = connection.execute(
            "SELECT type, date, notes FROM activities "
            "WHERE application_id = ?",
            (application_id,),
        ).fetchall()
        assert result == {"stage_changed": True}
        assert row["stage"] == "Contacted"
        assert row["next_action"] == "Email"
        assert len(activities) == 2
        assert activities[-1]["type"] == "stage_change"
        assert activities[-1]["date"] == "2026-11-20"
        assert activities[-1]["notes"] == "To research -> Contacted"
    finally:
        connection.close()


def test_same_stage_updates_fields_without_activity(tmp_path):
    connection, _, _, application_id, _ = seed_database(tmp_path)
    try:
        before = connection.execute(
            "SELECT COUNT(*) FROM activities WHERE application_id = ?",
            (application_id,),
        ).fetchone()[0]
        save_application(
            connection,
            application_id,
            stage="To research",
            on_date="2026-11-20",
            next_action="Review",
            next_action_date="20261121",
            notes="Updated",
        )
        row = connection.execute(
            "SELECT next_action, next_action_date, notes FROM applications "
            "WHERE id = ?",
            (application_id,),
        ).fetchone()
        after = connection.execute(
            "SELECT COUNT(*) FROM activities WHERE application_id = ?",
            (application_id,),
        ).fetchone()[0]
        assert row["next_action"] == "Review"
        assert row["next_action_date"] == "2026-11-21"
        assert row["notes"] == "Updated"
        assert after == before
    finally:
        connection.close()


def test_applied_sets_submitted_date_only_when_empty(tmp_path):
    connection, _, _, application_id, _ = seed_database(tmp_path)
    try:
        save_application(
            connection,
            application_id,
            stage="Applied",
            on_date="2026-11-20",
            next_action=None,
            next_action_date=None,
            notes=None,
        )
        row = connection.execute(
            "SELECT submitted_date FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        assert row["submitted_date"] == "2026-11-20"

        save_application(
            connection,
            application_id,
            stage="Applied",
            on_date="2026-12-01",
            next_action=None,
            next_action_date=None,
            notes=None,
        )
        row = connection.execute(
            "SELECT submitted_date FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        assert row["submitted_date"] == "2026-11-20"
    finally:
        connection.close()


@pytest.mark.parametrize("bad_stage", ["Unknown", "contacted", ""])
def test_invalid_stage_changes_nothing(tmp_path, bad_stage):
    connection, _, _, application_id, _ = seed_database(tmp_path)
    try:
        before = connection.execute(
            "SELECT stage FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()["stage"]
        before_count = connection.execute(
            "SELECT COUNT(*) FROM activities WHERE application_id = ?",
            (application_id,),
        ).fetchone()[0]
        with pytest.raises(ValueError):
            save_application(
                connection,
                application_id,
                stage=bad_stage,
                on_date="2026-11-20",
                next_action="Email",
                next_action_date="",
                notes="",
            )
        after = connection.execute(
            "SELECT stage FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()["stage"]
        after_count = connection.execute(
            "SELECT COUNT(*) FROM activities WHERE application_id = ?",
            (application_id,),
        ).fetchone()[0]
        assert after == before
        assert after_count == before_count
    finally:
        connection.close()


@pytest.mark.parametrize("bad_date", ["2026-13-45", "tomorrow"])
def test_invalid_dates_change_nothing(tmp_path, bad_date):
    connection, _, _, application_id, _ = seed_database(tmp_path)
    try:
        before = connection.execute(
            "SELECT stage, next_action, next_action_date, notes "
            "FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        before_count = connection.execute(
            "SELECT COUNT(*) FROM activities WHERE application_id = ?",
            (application_id,),
        ).fetchone()[0]
        with pytest.raises(ValueError):
            save_application(
                connection,
                application_id,
                stage="Contacted",
                on_date=bad_date,
                next_action="Email",
                next_action_date="",
                notes="Updated",
            )
        after = connection.execute(
            "SELECT stage, next_action, next_action_date, notes "
            "FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        after_count = connection.execute(
            "SELECT COUNT(*) FROM activities WHERE application_id = ?",
            (application_id,),
        ).fetchone()[0]
        assert tuple(after) == tuple(before)
        assert after_count == before_count
    finally:
        connection.close()


def test_invalid_next_action_date_changes_nothing(tmp_path):
    connection, _, _, application_id, _ = seed_database(tmp_path)
    try:
        before = connection.execute(
            "SELECT next_action_date FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()["next_action_date"]
        with pytest.raises(ValueError):
            save_application(
                connection,
                application_id,
                stage="To research",
                on_date="2026-11-20",
                next_action="Email",
                next_action_date="tomorrow",
                notes="",
            )
        after = connection.execute(
            "SELECT next_action_date FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()["next_action_date"]
        assert after == before
    finally:
        connection.close()


def test_empty_strings_become_null_and_compact_date_is_canonical(tmp_path):
    connection, _, _, application_id, _ = seed_database(tmp_path)
    try:
        save_application(
            connection,
            application_id,
            stage="To research",
            on_date="20261119",
            next_action="",
            next_action_date="",
            notes="",
        )
        row = connection.execute(
            "SELECT next_action, next_action_date, notes "
            "FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        assert row["next_action"] is None
        assert row["next_action_date"] is None
        assert row["notes"] is None

        save_application(
            connection,
            application_id,
            stage="To research",
            on_date="20261119",
            next_action=None,
            next_action_date="20261201",
            notes=None,
        )
        row = connection.execute(
            "SELECT next_action_date FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        assert row["next_action_date"] == "2026-12-01"
    finally:
        connection.close()


def test_unknown_ids_raise_lookup_error(tmp_path):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        with pytest.raises(LookupError):
            save_application(
                connection,
                -1,
                stage="Contacted",
                on_date="2026-11-20",
                next_action=None,
                next_action_date=None,
                notes=None,
            )
        before = connection.execute(
            "SELECT COUNT(*) FROM applications"
        ).fetchone()[0]
        with pytest.raises(LookupError):
            add_application(connection, -1, on_date="2026-11-20")
        after = connection.execute(
            "SELECT COUNT(*) FROM applications"
        ).fetchone()[0]
        assert after == before
    finally:
        connection.close()


def test_add_application_and_add_note_create_activities(tmp_path):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        application_id = add_application(
            connection,
            firm_a,
            stage="Contacted",
            on_date="2026-11-20",
        )
        activity_id = add_note(
            connection,
            application_id,
            "Called the office",
            on_date="2026-11-21",
        )
        activities = connection.execute(
            "SELECT type, date, notes FROM activities "
            "WHERE application_id = ? ORDER BY id",
            (application_id,),
        ).fetchall()
        assert activities[0]["type"] == "application_created"
        assert activities[0]["date"] == "2026-11-20"
        assert activities[1]["type"] == "note"
        assert activities[1]["notes"] == "Called the office"
        assert activity_id > 0
    finally:
        connection.close()


def test_add_note_rejects_empty_text(tmp_path):
    connection, _, _, application_id, _ = seed_database(tmp_path)
    try:
        before = connection.execute(
            "SELECT COUNT(*) FROM activities"
        ).fetchone()[0]
        with pytest.raises(ValueError):
            add_note(connection, application_id, "   ", "2026-11-20")
        after = connection.execute(
            "SELECT COUNT(*) FROM activities"
        ).fetchone()[0]
        assert after == before
    finally:
        connection.close()


def test_change_application_never_modifies_other_application(tmp_path):
    connection, firm_a, _, application_a, application_b = seed_database(tmp_path)
    try:
        save_application(
            connection,
            application_a,
            stage="Contacted",
            on_date="2026-11-20",
            next_action="Email",
            next_action_date="2026-11-21",
            notes="Changed",
        )
        other = connection.execute(
            "SELECT stage, next_action, notes "
            "FROM applications WHERE id = ?",
            (application_b,),
        ).fetchone()
        assert other["stage"] == "To research"
        assert other["next_action"] is None
        assert other["notes"] is None
    finally:
        connection.close()


def test_stage_change_is_atomic_if_activity_insert_fails(tmp_path):
    connection, _, _, application_id, _ = seed_database(tmp_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER reject_unknown_activity
            BEFORE INSERT ON activities
            WHEN NEW.type NOT IN (
                'application_created', 'stage_change', 'note'
            )
            BEGIN
                SELECT RAISE(ABORT, 'unknown activity type');
            END
            """
        )
        connection.execute(
            "CREATE TEMP TRIGGER fail_stage_change "
            "BEFORE INSERT ON activities "
            "WHEN NEW.type = 'stage_change' "
            "BEGIN SELECT RAISE(ABORT, 'forced activity failure'); END"
        )
        before = connection.execute(
            "SELECT stage FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()["stage"]
        with pytest.raises(sqlite3.IntegrityError):
            save_application(
                connection,
                application_id,
                stage="Contacted",
                on_date="2026-11-20",
                next_action="Email",
                next_action_date="2026-11-21",
                notes="Changed",
            )
        after = connection.execute(
            "SELECT stage FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()["stage"]
        activity_count = connection.execute(
            "SELECT COUNT(*) FROM activities "
            "WHERE application_id = ? AND type = 'stage_change'",
            (application_id,),
        ).fetchone()[0]
        assert after == before
        assert activity_count == 0
    finally:
        connection.close()
