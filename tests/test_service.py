"""Offline tests for application write services."""

import sqlite3

import pytest

from tracker.adapters.base import AdapterResult, Listing
from tracker.checks import apply_check_result
from tracker.db import connect, init_db
from tracker.models import (
    ACTIVITY_CREATED,
    ACTIVITY_NOTE,
    ACTIVITY_STAGE_CHANGE,
)
from tracker.service import (
    add_manual_opportunity,
    add_application,
    add_note,
    delete_manual_opportunity,
    MANUAL_CHECK_EVIDENCE,
    record_manual_check,
    save_application,
    set_application_opportunity,
    set_opportunity_state,
    update_firm_link,
    update_manual_opportunity,
)


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


def test_clearing_next_action_date_keeps_stage_and_writes_no_activity(tmp_path):
    connection, _, _, application_id, _ = seed_database(tmp_path)
    try:
        save_application(
            connection,
            application_id,
            stage="To research",
            on_date="2026-11-20",
            next_action="Review",
            next_action_date="2026-11-21",
            notes="",
        )
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
            next_action_date=None,
            notes="",
        )

        row = connection.execute(
            "SELECT stage, next_action_date FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        after = connection.execute(
            "SELECT COUNT(*) FROM activities WHERE application_id = ?",
            (application_id,),
        ).fetchone()[0]
        assert row["stage"] == "To research"
        assert row["next_action_date"] is None
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


def test_named_activity_constants():
    assert ACTIVITY_CREATED == "application_created"
    assert ACTIVITY_STAGE_CHANGE == "stage_change"
    assert ACTIVITY_NOTE == "note"


def test_record_manual_check_updates_firm_without_changing_opportunities(tmp_path):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        connection.execute(
            """
            INSERT INTO opportunities (
                firm_id, title, location, url, state, first_seen, last_seen,
                missing_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                firm_a,
                "Sample role",
                "Paris",
                "https://example.test/role",
                "Open",
                "2026-11-01",
                "2026-11-19",
                2,
            ),
        )
        connection.commit()
        before_opportunity = connection.execute(
            """
            SELECT state, missing_count, last_seen
            FROM opportunities
            WHERE firm_id = ?
            """,
            (firm_a,),
        ).fetchone()

        check_id = record_manual_check(
            connection,
            firm_a,
            "2026-11-20T13:35:55",
        )

        check = connection.execute(
            """
            SELECT firm_id, started_at, finished_at, result, http_status,
                   n_results, error, evidence_snippet
            FROM checks
            WHERE id = ?
            """,
            (check_id,),
        ).fetchone()
        firm = connection.execute(
            """
            SELECT last_check_status, last_successful_check
            FROM firms
            WHERE id = ?
            """,
            (firm_a,),
        ).fetchone()
        after_opportunity = connection.execute(
            """
            SELECT state, missing_count, last_seen
            FROM opportunities
            WHERE firm_id = ?
            """,
            (firm_a,),
        ).fetchone()

        assert tuple(check) == (
            firm_a,
            "2026-11-20T13:35:55",
            "2026-11-20T13:35:55",
            "ok",
            None,
            None,
            None,
            MANUAL_CHECK_EVIDENCE,
        )
        assert tuple(firm) == ("ok", "2026-11-20T13:35:55")
        assert tuple(after_opportunity) == tuple(before_opportunity)
        assert connection.execute(
            "SELECT COUNT(*) FROM checks WHERE firm_id = ?",
            (firm_a,),
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize("bad_timestamp", ["not-a-date", "2026-13-40T00:00:00"])
def test_record_manual_check_rejects_bad_timestamp_without_writes(
    tmp_path,
    bad_timestamp,
):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        with pytest.raises(ValueError):
            record_manual_check(connection, firm_a, bad_timestamp)
        assert connection.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == 0
        firm = connection.execute(
            """
            SELECT last_check_status, last_successful_check
            FROM firms
            WHERE id = ?
            """,
            (firm_a,),
        ).fetchone()
        assert tuple(firm) == (None, None)
    finally:
        connection.close()


def test_record_manual_check_unknown_firm_writes_nothing(tmp_path):
    connection, _, _, _, _ = seed_database(tmp_path)
    try:
        with pytest.raises(LookupError):
            record_manual_check(connection, -1, "2026-11-20T13:35:55")
        assert connection.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == 0
    finally:
        connection.close()


def test_valid_firm_link_updates_url_and_type(tmp_path):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        result = update_firm_link(
            connection,
            firm_a,
            "https://example.test/careers",
            "careers_page",
        )
        row = connection.execute(
            "SELECT careers_url, source_type FROM firms WHERE id = ?",
            (firm_a,),
        ).fetchone()
        assert result == {"updated": 1}
        assert row["careers_url"] == "https://example.test/careers"
        assert row["source_type"] == "careers_page"
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("bad_url", "bad_link_type"),
    [
        ("javascript:alert(1)", "careers_page"),
        ("example.test/jobs", "careers_page"),
        ("https://", "careers_page"),
        ("https:///x", "careers_page"),
        ("https://example.test/a b", "careers_page"),
        ("", "careers_page"),
        ("https://example.test/" + "x" * 2001, "careers_page"),
        ("https://example.test/careers", "search_link"),
        ("https://example.test/careers", "other"),
    ],
)
def test_invalid_firm_link_changes_nothing(tmp_path, bad_url, bad_link_type):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        connection.execute(
            "UPDATE firms SET careers_url = ?, source_type = ?, last_verified = ? "
            "WHERE id = ?",
            ("https://example.test/old", "search_link", "2026-11-01", firm_a),
        )
        connection.commit()
        before = connection.execute(
            "SELECT careers_url, source_type, last_verified "
            "FROM firms WHERE id = ?",
            (firm_a,),
        ).fetchone()
        with pytest.raises(ValueError):
            update_firm_link(
                connection,
                firm_a,
                bad_url,
                bad_link_type,
            )
        after = connection.execute(
            "SELECT careers_url, source_type, last_verified "
            "FROM firms WHERE id = ?",
            (firm_a,),
        ).fetchone()
        assert tuple(after) == tuple(before)
    finally:
        connection.close()


def test_unknown_firm_link_id_raises_lookup_error(tmp_path):
    connection, _, _, _, _ = seed_database(tmp_path)
    try:
        with pytest.raises(LookupError):
            update_firm_link(
                connection,
                -1,
                "https://example.test/careers",
                "careers_page",
            )
    finally:
        connection.close()


def test_apply_link_to_matching_offices(tmp_path):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        connection.execute(
            "UPDATE firms SET employer_key = 'example employer', "
            "source_type = 'search_link' WHERE id = ?",
            (firm_a,),
        )
        search_link_id = None
        null_source_id = None
        protected_id = None
        for country, source_type in (
            ("Spain", "search_link"),
            ("Switzerland", None),
            ("Portugal", "careers_page"),
        ):
            connection.execute(
                """
                INSERT INTO firms (
                    profile, name, employer_key, country, city, source_type
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "profile",
                    "Example Firm",
                    "example employer",
                    country,
                    "Capital",
                    source_type,
                ),
            )
            inserted_id = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            if country == "Spain":
                search_link_id = inserted_id
            elif country == "Switzerland":
                null_source_id = inserted_id
            else:
                protected_id = inserted_id

        connection.execute(
            """
            INSERT INTO firms (
                profile, name, employer_key, country, city, source_type
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "profile",
                "Example Firm",
                "different employer",
                "France",
                "Lyon",
                "search_link",
            ),
        )
        different_employer_id = connection.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

        connection.execute(
            """
            INSERT INTO firms (
                profile, name, employer_key, country, city, source_type
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "other-profile",
                "Example Firm",
                "example employer",
                "Italy",
                "Rome",
                "search_link",
            ),
        )
        different_profile_id = connection.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]
        connection.commit()

        result = update_firm_link(
            connection,
            firm_a,
            "https://example.test/careers",
            "careers_page",
            apply_to_other_offices=True,
        )
        assert result == {"updated": 3}

        rows = {
            row["id"]: row
            for row in connection.execute(
                "SELECT id, careers_url, source_type FROM firms"
            )
        }
        assert (
            rows[search_link_id]["careers_url"],
            rows[search_link_id]["source_type"],
        ) == ("https://example.test/careers", "careers_page")
        assert (
            rows[null_source_id]["careers_url"],
            rows[null_source_id]["source_type"],
        ) == ("https://example.test/careers", "careers_page")
        assert rows[protected_id]["source_type"] == "careers_page"
        assert rows[different_employer_id]["source_type"] == "search_link"
        assert rows[different_profile_id]["source_type"] == "search_link"
    finally:
        connection.close()


def test_apply_link_false_leaves_other_offices_untouched(tmp_path):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        connection.execute(
            "UPDATE firms SET employer_key = 'example employer', "
            "source_type = 'search_link' WHERE id = ?",
            (firm_a,),
        )
        connection.execute(
            """
            INSERT INTO firms (
                profile, name, employer_key, country, city, source_type
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "profile",
                "Example Firm",
                "example employer",
                "Spain",
                "Madrid",
                "search_link",
            ),
        )
        other_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.commit()

        result = update_firm_link(
            connection,
            firm_a,
            "https://example.test/careers",
            "direct_posting",
            apply_to_other_offices=False,
        )
        other = connection.execute(
            "SELECT careers_url, source_type FROM firms WHERE id = ?",
            (other_id,),
        ).fetchone()
        assert result == {"updated": 1}
        assert other["careers_url"] is None
        assert other["source_type"] == "search_link"
    finally:
        connection.close()


def test_link_update_does_not_change_last_verified(tmp_path):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        connection.execute(
            "UPDATE firms SET last_verified = '2026-11-01' WHERE id = ?",
            (firm_a,),
        )
        connection.commit()
        update_firm_link(
            connection,
            firm_a,
            "https://example.test/careers",
            "careers_page",
        )
        last_verified = connection.execute(
            "SELECT last_verified FROM firms WHERE id = ?",
            (firm_a,),
        ).fetchone()["last_verified"]
        assert last_verified == "2026-11-01"
    finally:
        connection.close()


def test_add_manual_opportunity_stores_fields_and_unique_fingerprints(tmp_path):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        first_id = add_manual_opportunity(
            connection,
            firm_a,
            "  Risk Intern  ",
            url="https://example.test/role",
            location="  Paris  ",
            deadline="20261201",
            notes="  Synthetic note  ",
            state="Open",
            on_date="20261120",
        )
        second_id = add_manual_opportunity(
            connection,
            firm_a,
            "Risk Intern",
            url="https://example.test/role",
            location="Paris",
            deadline="2026-12-01",
            notes="Synthetic note",
            state="Open",
            on_date="2026-11-20",
        )
        rows = connection.execute(
            "SELECT * FROM opportunities "
            "WHERE id IN (?, ?) ORDER BY id",
            (first_id, second_id),
        ).fetchall()
        assert len(rows) == 2
        assert rows[0]["title"] == "Risk Intern"
        assert rows[0]["location"] == "Paris"
        assert rows[0]["url"] == "https://example.test/role"
        assert rows[0]["deadline"] == "2026-12-01"
        assert rows[0]["description_text"] == "Synthetic note"
        assert rows[0]["state"] == "Open"
        assert rows[0]["first_seen"] == "2026-11-20"
        assert rows[0]["last_seen"] == "2026-11-20"
        assert rows[0]["missing_count"] == 0
        assert rows[0]["fingerprint"].startswith("manual:")
        assert rows[1]["fingerprint"].startswith("manual:")
        assert rows[0]["fingerprint"] != rows[1]["fingerprint"]
        assert rows[0]["evidence"] == "manual: added by user"
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("title", "url", "deadline", "state"),
    [
        ("", "https://example.test/role", None, "Open"),
        ("x" * 301, "https://example.test/role", None, "Open"),
        ("Role", "javascript:alert(1)", None, "Open"),
        ("Role", "example.test/x", None, "Open"),
        ("Role", "https:///x", None, "Open"),
        ("Role", None, "tomorrow", "Open"),
        ("Role", None, "2026-13-45", "Open"),
        ("Role", None, None, "Weird"),
    ],
)
def test_add_manual_opportunity_invalid_values_write_nothing(
    tmp_path,
    title,
    url,
    deadline,
    state,
):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        before = connection.execute(
            "SELECT COUNT(*) FROM opportunities"
        ).fetchone()[0]
        with pytest.raises(ValueError):
            add_manual_opportunity(
                connection,
                firm_a,
                title,
                url=url,
                deadline=deadline,
                state=state,
            )
        after = connection.execute(
            "SELECT COUNT(*) FROM opportunities"
        ).fetchone()[0]
        assert after == before
    finally:
        connection.close()


def test_add_manual_opportunity_empty_optional_fields_and_unknown_firm(tmp_path):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        opportunity_id = add_manual_opportunity(
            connection,
            firm_a,
            "Role",
            url=" ",
            location=" ",
            deadline=" ",
            notes=" ",
        )
        row = connection.execute(
            "SELECT url, location, deadline, description_text "
            "FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        assert row["url"] is None
        assert row["location"] is None
        assert row["deadline"] is None
        assert row["description_text"] is None

        before = connection.execute(
            "SELECT COUNT(*) FROM opportunities"
        ).fetchone()[0]
        with pytest.raises(LookupError):
            add_manual_opportunity(connection, -1, "Unknown")
        after = connection.execute(
            "SELECT COUNT(*) FROM opportunities"
        ).fetchone()[0]
        assert after == before
    finally:
        connection.close()


def test_update_manual_opportunity_changes_fields(tmp_path):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        opportunity_id = add_manual_opportunity(
            connection,
            firm_a,
            "Old title",
            state="Open",
        )
        update_manual_opportunity(
            connection,
            opportunity_id,
            title=" New title ",
            url="https://example.test/new",
            location="Zurich",
            deadline="20261201",
            notes="Updated",
            state="Upcoming",
        )
        row = connection.execute(
            "SELECT title, url, location, deadline, description_text, state "
            "FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        assert row["title"] == "New title"
        assert row["url"] == "https://example.test/new"
        assert row["location"] == "Zurich"
        assert row["deadline"] == "2026-12-01"
        assert row["description_text"] == "Updated"
        assert row["state"] == "Upcoming"

        with pytest.raises(LookupError):
            update_manual_opportunity(
                connection,
                -1,
                title="Missing",
                url=None,
                location=None,
                deadline=None,
                notes=None,
                state="Open",
            )
    finally:
        connection.close()


def test_update_manual_opportunity_refuses_automatic_row(tmp_path):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        apply_check_result(
            connection,
            firm_a,
            AdapterResult(
                "ok",
                (Listing(
                    source_job_id="job-1",
                    title="Automatic role",
                    location_raw="Zurich",
                    url="https://example.test/job-1",
                    posted_date=None,
                    description="Synthetic",
                    language="en",
                ),),
                ("Synthetic evidence",),
                None,
                200,
            ),
            started_at="2026-11-20T10:00:00",
            finished_at="2026-11-20T10:01:00",
        )
        opportunity = connection.execute(
            "SELECT id, title FROM opportunities WHERE firm_id = ?",
            (firm_a,),
        ).fetchone()
        before = tuple(opportunity)
        with pytest.raises(
            ValueError,
            match="only manual opportunities can be edited",
        ):
            update_manual_opportunity(
                connection,
                opportunity["id"],
                title="Changed",
                url=None,
                location=None,
                deadline=None,
                notes=None,
                state="Open",
            )
        after = connection.execute(
            "SELECT id, title FROM opportunities WHERE id = ?",
            (opportunity["id"],),
        ).fetchone()
        assert tuple(after) == before
    finally:
        connection.close()


def test_delete_manual_opportunity_and_application_link_rules(tmp_path):
    connection, firm_a, firm_b, application_a, application_b = seed_database(tmp_path)
    try:
        manual_id = add_manual_opportunity(connection, firm_a, "Manual role")
        delete_manual_opportunity(connection, manual_id)
        assert connection.execute(
            "SELECT COUNT(*) FROM opportunities WHERE id = ?",
            (manual_id,),
        ).fetchone()[0] == 0

        linked_manual_id = add_manual_opportunity(connection, firm_a, "Linked role")
        set_application_opportunity(connection, application_a, linked_manual_id)
        before = connection.execute(
            "SELECT COUNT(*) FROM applications WHERE opportunity_id = ?",
            (linked_manual_id,),
        ).fetchone()[0]
        with pytest.raises(ValueError):
            delete_manual_opportunity(connection, linked_manual_id)
        assert connection.execute(
            "SELECT COUNT(*) FROM opportunities WHERE id = ?",
            (linked_manual_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM applications WHERE opportunity_id = ?",
            (linked_manual_id,),
        ).fetchone()[0] == before

        automatic_id = connection.execute(
            "INSERT INTO opportunities (firm_id, fingerprint, title) "
            "VALUES (?, ?, ?)",
            (firm_a, "fp:auto", "Automatic role"),
        ).lastrowid
        with pytest.raises(ValueError):
            delete_manual_opportunity(connection, automatic_id)

        set_application_opportunity(connection, application_a, None)
        assert connection.execute(
            "SELECT opportunity_id FROM applications WHERE id = ?",
            (application_a,),
        ).fetchone()["opportunity_id"] is None

        other_manual_id = add_manual_opportunity(connection, firm_b, "Other firm role")
        with pytest.raises(ValueError):
            set_application_opportunity(connection, application_a, other_manual_id)
    finally:
        connection.close()


def test_set_opportunity_state_changes_only_state_for_manual_and_automatic(tmp_path):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        manual_id = add_manual_opportunity(
            connection,
            firm_a,
            "Manual role",
            url="https://example.test/manual",
            location="Zurich",
            state="Open",
        )
        apply_check_result(
            connection,
            firm_a,
            AdapterResult(
                "ok",
                (Listing(
                    source_job_id="job-1",
                    title="Automatic role",
                    location_raw="Zurich",
                    url="https://example.test/job-1",
                    posted_date=None,
                    description="Synthetic",
                    language="en",
                ),),
                ("Synthetic evidence",),
                None,
                200,
            ),
            started_at="2026-11-20T10:00:00",
            finished_at="2026-11-20T10:01:00",
        )
        automatic_id = connection.execute(
            "SELECT id FROM opportunities "
            "WHERE fingerprint NOT LIKE 'manual:%'"
        ).fetchone()["id"]

        for opportunity_id in (manual_id, automatic_id):
            before = dict(connection.execute(
                "SELECT * FROM opportunities WHERE id = ?",
                (opportunity_id,),
            ).fetchone())
            set_opportunity_state(connection, opportunity_id, "No match")
            after = dict(connection.execute(
                "SELECT * FROM opportunities WHERE id = ?",
                (opportunity_id,),
            ).fetchone())

            assert after["state"] == "No match"
            for column in before:
                if column != "state":
                    assert after[column] == before[column]
    finally:
        connection.close()


def test_set_opportunity_state_invalid_and_unknown_do_not_change(tmp_path):
    connection, firm_a, _, _, _ = seed_database(tmp_path)
    try:
        opportunity_id = add_manual_opportunity(
            connection,
            firm_a,
            "Manual role",
            state="Open",
        )
        before = dict(connection.execute(
            "SELECT * FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone())

        with pytest.raises(ValueError):
            set_opportunity_state(connection, opportunity_id, "Weird")

        after_invalid = dict(connection.execute(
            "SELECT * FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone())
        assert after_invalid == before

        with pytest.raises(LookupError):
            set_opportunity_state(connection, -1, "Open")
        assert dict(connection.execute(
            "SELECT * FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()) == before
    finally:
        connection.close()


def test_set_application_opportunity_same_firm_clear_and_errors(tmp_path):
    connection, firm_a, firm_b, application_a, _ = seed_database(tmp_path)
    try:
        same_id = add_manual_opportunity(connection, firm_a, "Same firm")
        set_application_opportunity(connection, application_a, same_id)
        assert connection.execute(
            "SELECT opportunity_id FROM applications WHERE id = ?",
            (application_a,),
        ).fetchone()["opportunity_id"] == same_id

        set_application_opportunity(connection, application_a, None)
        assert connection.execute(
            "SELECT opportunity_id FROM applications WHERE id = ?",
            (application_a,),
        ).fetchone()["opportunity_id"] is None

        other_id = add_manual_opportunity(connection, firm_b, "Other firm")
        before = connection.execute(
            "SELECT opportunity_id FROM applications WHERE id = ?",
            (application_a,),
        ).fetchone()["opportunity_id"]
        with pytest.raises(ValueError):
            set_application_opportunity(connection, application_a, other_id)
        assert connection.execute(
            "SELECT opportunity_id FROM applications WHERE id = ?",
            (application_a,),
        ).fetchone()["opportunity_id"] == before

        with pytest.raises(LookupError):
            set_application_opportunity(connection, -1, same_id)
        with pytest.raises(LookupError):
            set_application_opportunity(connection, application_a, -1)

        before_activity_count = connection.execute(
            "SELECT COUNT(*) FROM activities"
        ).fetchone()[0]
        set_application_opportunity(connection, application_a, same_id)
        assert connection.execute(
            "SELECT COUNT(*) FROM activities"
        ).fetchone()[0] == before_activity_count
    finally:
        connection.close()
