"""Offline tests for adapter check storage and opportunity updates."""

import sqlite3

import pytest

from tracker.adapters.base import AdapterResult, Listing
from tracker.checks import apply_check_result, make_identity
from tracker.db import connect, init_db
from tracker.service import add_manual_opportunity


STARTED = "2026-11-20T10:00:00"
FINISHED_1 = "2026-11-20T10:01:00"
FINISHED_2 = "2026-11-20T10:02:00"
FINISHED_3 = "2026-11-20T10:03:00"


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
    connection.commit()
    return connection, firm_a, firm_b


def listing(
    title="Analyst Intern",
    location="Zurich",
    url="https://example.test/jobs/analyst",
    description="Synthetic description",
    source_job_id=None,
):
    return Listing(
        source_job_id=source_job_id,
        title=title,
        location_raw=location,
        url=url,
        posted_date=None,
        description=description,
        language="en",
    )


def ok_result(*listings, evidence=("Synthetic evidence",)):
    return AdapterResult("ok", tuple(listings), tuple(evidence), None, 200)


def test_new_listing_is_inserted_in_needs_review_state(tmp_path):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        result = apply_check_result(
            connection,
            firm_id,
            ok_result(listing()),
            started_at=STARTED,
            finished_at=FINISHED_1,
        )
        row = connection.execute(
            "SELECT state, first_seen, last_seen, missing_count, evidence "
            "FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        assert result["new"] == 1
        assert row["state"] == "Needs review"
        assert row["first_seen"] == FINISHED_1
        assert row["last_seen"] == FINISHED_1
        assert row["missing_count"] == 0
        assert row["evidence"] == "Synthetic evidence"
    finally:
        connection.close()


def test_same_result_is_unchanged_and_updates_last_seen(tmp_path):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        apply_check_result(connection, firm_id, ok_result(listing()), started_at=STARTED, finished_at=FINISHED_1)
        result = apply_check_result(connection, firm_id, ok_result(listing()), started_at=STARTED, finished_at=FINISHED_2)
        row = connection.execute(
            "SELECT last_seen, missing_count FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        count = connection.execute(
            "SELECT COUNT(*) FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()[0]
        assert result["unchanged"] == 1
        assert result["duplicates"] == 0
        assert row["last_seen"] == FINISHED_2
        assert row["missing_count"] == 0
        assert count == 1

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO opportunities (firm_id, fingerprint)
                VALUES (?, ?)
                """,
                (firm_id, make_identity(listing())),
            )
    finally:
        connection.close()


def test_changed_fields_are_updated_and_counted(tmp_path):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        apply_check_result(
            connection,
            firm_id,
            ok_result(listing(source_job_id="job-1")),
            started_at=STARTED,
            finished_at=FINISHED_1,
        )
        changed = listing(
            title="  Risk Analyst Intern  ",
            location="  Geneva  ",
            url="https://example.test/jobs/risk",
            description="  Updated description  ",
            source_job_id="job-1",
        )
        result = apply_check_result(connection, firm_id, ok_result(changed), started_at=STARTED, finished_at=FINISHED_2)
        row = connection.execute(
            "SELECT title, location, url, description_text FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        assert result["changed"] == 1
        assert row["title"] == "Risk Analyst Intern"
        assert row["location"] == "Geneva"
        assert row["url"] == "https://example.test/jobs/risk"
        assert row["description_text"] == "Updated description"
    finally:
        connection.close()


def test_missing_counts_reset_and_close_on_third_consecutive_miss(tmp_path):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        apply_check_result(connection, firm_id, ok_result(listing()), started_at=STARTED, finished_at=FINISHED_1)
        apply_check_result(connection, firm_id, AdapterResult("empty"), started_at=STARTED, finished_at=FINISHED_2)
        apply_check_result(connection, firm_id, AdapterResult("empty"), started_at=STARTED, finished_at=FINISHED_3)
        third = apply_check_result(connection, firm_id, AdapterResult("empty"), started_at=STARTED, finished_at="2026-11-20T10:04:00")
        row = connection.execute(
            "SELECT missing_count, state FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        assert row["missing_count"] == 3
        assert row["state"] == "Closed"
        assert third["missing"] == 1
        assert third["closed"] == 1

        apply_check_result(connection, firm_id, ok_result(listing()), started_at=STARTED, finished_at="2026-11-20T10:05:00")
        apply_check_result(connection, firm_id, AdapterResult("empty"), started_at=STARTED, finished_at="2026-11-20T10:06:00")
        row = connection.execute(
            "SELECT missing_count, state FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        assert row["missing_count"] == 1
        assert row["state"] == "Needs review"
    finally:
        connection.close()


def test_empty_result_marks_all_missing_and_updates_successful_check(tmp_path):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        apply_check_result(connection, firm_id, ok_result(listing()), started_at=STARTED, finished_at=FINISHED_1)
        apply_check_result(connection, firm_id, ok_result(listing("Second")), started_at=STARTED, finished_at=FINISHED_2)
        result = apply_check_result(connection, firm_id, AdapterResult("empty"), started_at=STARTED, finished_at=FINISHED_3)
        firm = connection.execute(
            "SELECT last_check_status, last_successful_check FROM firms WHERE id = ?",
            (firm_id,),
        ).fetchone()
        assert result["missing"] == 2
        assert firm["last_check_status"] == "empty"
        assert firm["last_successful_check"] == FINISHED_3
    finally:
        connection.close()


@pytest.mark.parametrize("status", ["failed", "blocked"])
def test_failed_and_blocked_do_not_change_opportunities(tmp_path, status):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        apply_check_result(connection, firm_id, ok_result(listing()), started_at=STARTED, finished_at=FINISHED_1)
        before = connection.execute(
            "SELECT missing_count, state, last_seen, title, location, url, description_text "
            "FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        result = AdapterResult(status, (), (), "source refused", 403)
        summary = apply_check_result(connection, firm_id, result, started_at=STARTED, finished_at=FINISHED_2)
        after = connection.execute(
            "SELECT missing_count, state, last_seen, title, location, url, description_text "
            "FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        firm = connection.execute(
            "SELECT last_check_status, last_successful_check FROM firms WHERE id = ?",
            (firm_id,),
        ).fetchone()
        check = connection.execute(
            "SELECT result, n_results, error FROM checks "
            "WHERE firm_id = ? ORDER BY id DESC",
            (firm_id,),
        ).fetchone()
        assert tuple(after) == tuple(before)
        assert firm["last_check_status"] == status
        assert firm["last_successful_check"] == FINISHED_1
        assert check["result"] == status
        assert check["n_results"] is None
        assert check["error"] == "source refused"
        assert summary["missing"] == 0
    finally:
        connection.close()


def test_no_match_listing_stays_no_match_after_three_misses(tmp_path):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        apply_check_result(
            connection,
            firm_id,
            ok_result(listing()),
            started_at=STARTED,
            finished_at=FINISHED_1,
        )
        connection.execute(
            "UPDATE opportunities SET state = 'No match' WHERE firm_id = ?",
            (firm_id,),
        )

        third = None
        for index, finished_at in enumerate(
            (FINISHED_2, FINISHED_3, "2026-11-20T10:04:00"),
            start=1,
        ):
            third = apply_check_result(
                connection,
                firm_id,
                AdapterResult("empty"),
                started_at=STARTED,
                finished_at=finished_at,
            )

        row = connection.execute(
            "SELECT missing_count, state FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        assert row["missing_count"] == 3
        assert row["state"] == "No match"
        assert third["closed"] == 0
    finally:
        connection.close()


def test_no_match_listing_reappearing_stays_no_match(tmp_path):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        apply_check_result(
            connection,
            firm_id,
            ok_result(listing()),
            started_at=STARTED,
            finished_at=FINISHED_1,
        )
        connection.execute(
            "UPDATE opportunities SET state = 'No match', missing_count = 2 "
            "WHERE firm_id = ?",
            (firm_id,),
        )

        result = apply_check_result(
            connection,
            firm_id,
            ok_result(listing()),
            started_at=STARTED,
            finished_at=FINISHED_2,
        )
        row = connection.execute(
            "SELECT state, missing_count, last_seen "
            "FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        assert row["state"] == "No match"
        assert row["missing_count"] == 0
        assert row["last_seen"] == FINISHED_2
        assert result["reopened"] == 0
    finally:
        connection.close()


def test_closed_listing_reopens_when_seen_again(tmp_path):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        apply_check_result(connection, firm_id, ok_result(listing()), started_at=STARTED, finished_at=FINISHED_1)
        connection.execute(
            "UPDATE opportunities SET state = 'Closed', missing_count = 3 WHERE firm_id = ?",
            (firm_id,),
        )
        result = apply_check_result(connection, firm_id, ok_result(listing()), started_at=STARTED, finished_at=FINISHED_2)
        row = connection.execute(
            "SELECT state, missing_count FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        assert row["state"] == "Needs review"
        assert row["missing_count"] == 0
        assert result["reopened"] == 1
    finally:
        connection.close()


def test_identity_normalization_and_source_job_id():
    base = listing(
        title="  Économiste  ",
        location="  Zürïch  ",
        url="https://example.test/jobs/Analyst/?query=1#part",
    )
    equivalent = listing(
        title="ECONOMISTE",
        location="zurich",
        url="https://example.test/jobs/analyst",
    )
    different = listing(title="Data Analyst")
    by_id = listing(title="One title", source_job_id="  Job-42  ")
    by_id_other_title = listing(title="Another title", source_job_id="Job-42")

    assert make_identity(base) == make_identity(equivalent)
    assert make_identity(base) != make_identity(different)
    assert make_identity(by_id) == "id:Job-42"
    assert make_identity(by_id) == make_identity(by_id_other_title)


def test_duplicates_inside_result_are_stored_once(tmp_path):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        same = listing()
        result = apply_check_result(
            connection,
            firm_id,
            ok_result(same, listing(title="  ANALYST INTERN ", location="Zurich")),
            started_at=STARTED,
            finished_at=FINISHED_1,
        )
        count = connection.execute(
            "SELECT COUNT(*) FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()[0]
        assert count == 1
        assert result["new"] == 1
        assert result["duplicates"] == 1
    finally:
        connection.close()


def test_two_firms_do_not_affect_each_other(tmp_path):
    connection, firm_a, firm_b = seed_database(tmp_path)
    try:
        apply_check_result(connection, firm_a, ok_result(listing()), started_at=STARTED, finished_at=FINISHED_1)
        result = apply_check_result(connection, firm_b, AdapterResult("empty"), started_at=STARTED, finished_at=FINISHED_2)
        count_a = connection.execute(
            "SELECT COUNT(*) FROM opportunities WHERE firm_id = ?",
            (firm_a,),
        ).fetchone()[0]
        count_b = connection.execute(
            "SELECT COUNT(*) FROM opportunities WHERE firm_id = ?",
            (firm_b,),
        ).fetchone()[0]
        assert count_a == 1
        assert count_b == 0
        assert result["missing"] == 0
    finally:
        connection.close()


def test_invalid_adapter_result_is_failed_and_changes_nothing(tmp_path):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        apply_check_result(connection, firm_id, ok_result(listing()), started_at=STARTED, finished_at=FINISHED_1)
        before = connection.execute(
            "SELECT missing_count, state, last_seen, title FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        result = apply_check_result(
            connection,
            firm_id,
            AdapterResult("ok", (), ("ignored",)),
            started_at=STARTED,
            finished_at=FINISHED_2,
        )
        after = connection.execute(
            "SELECT missing_count, state, last_seen, title FROM opportunities WHERE firm_id = ?",
            (firm_id,),
        ).fetchone()
        check = connection.execute(
            "SELECT result, n_results, error FROM checks WHERE firm_id = ? ORDER BY id DESC",
            (firm_id,),
        ).fetchone()
        assert result["status"] == "failed"
        assert result["problems"]
        assert tuple(after) == tuple(before)
        assert check["result"] == "failed"
        assert check["n_results"] is None
        assert check["error"].startswith("invalid adapter result")
    finally:
        connection.close()


def test_unknown_firm_and_bad_timestamps_write_nothing(tmp_path):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        with pytest.raises(LookupError):
            apply_check_result(connection, 999999, AdapterResult("empty"), started_at=STARTED, finished_at=FINISHED_1)
        assert connection.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 0

        with pytest.raises(ValueError):
            apply_check_result(connection, firm_id, AdapterResult("empty"), started_at="not-a-date", finished_at=FINISHED_1)
        with pytest.raises(ValueError):
            apply_check_result(connection, firm_id, AdapterResult("empty"), started_at=FINISHED_2, finished_at=FINISHED_1)
        assert connection.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == 0
    finally:
        connection.close()


def test_checks_insert_failure_rolls_back_all_updates(tmp_path):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER reject_check_insert
            BEFORE INSERT ON checks
            BEGIN
                SELECT RAISE(ABORT, 'forced checks insert failure');
            END
            """
        )
        connection.commit()
        before_firm = connection.execute(
            "SELECT last_check_status, last_successful_check FROM firms WHERE id = ?",
            (firm_id,),
        ).fetchone()

        with pytest.raises(sqlite3.IntegrityError):
            apply_check_result(
                connection,
                firm_id,
                ok_result(listing()),
                started_at=STARTED,
                finished_at=FINISHED_1,
            )

        after_firm = connection.execute(
            "SELECT last_check_status, last_successful_check FROM firms WHERE id = ?",
            (firm_id,),
        ).fetchone()
        assert connection.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM opportunities WHERE firm_id = ?", (firm_id,)).fetchone()[0] == 0
        assert tuple(after_firm) == tuple(before_firm)
    finally:
        connection.close()


def test_manual_opportunity_is_untouched_by_all_check_results(tmp_path):
    connection, firm_id, _ = seed_database(tmp_path)
    try:
        manual_id = add_manual_opportunity(
            connection,
            firm_id,
            "Manual role",
            url="https://example.test/manual",
            location="Zurich",
            deadline="2026-12-01",
            notes="Manual note",
            state="Open",
            on_date="2026-11-20",
        )
        apply_check_result(
            connection,
            firm_id,
            ok_result(listing()),
            started_at=STARTED,
            finished_at=FINISHED_1,
        )
        manual_before = connection.execute(
            "SELECT state, missing_count, last_seen, title, url "
            "FROM opportunities WHERE id = ?",
            (manual_id,),
        ).fetchone()

        apply_check_result(
            connection,
            firm_id,
            ok_result(listing()),
            started_at=STARTED,
            finished_at=FINISHED_2,
        )
        manual_ok = connection.execute(
            "SELECT state, missing_count, last_seen, title, url "
            "FROM opportunities WHERE id = ?",
            (manual_id,),
        ).fetchone()
        assert tuple(manual_ok) == tuple(manual_before)

        for finished_at in (
            FINISHED_3,
            "2026-11-20T10:04:00",
            "2026-11-20T10:05:00",
        ):
            apply_check_result(
                connection,
                firm_id,
                AdapterResult("empty"),
                started_at=STARTED,
                finished_at=finished_at,
            )
        manual_after_empty = connection.execute(
            "SELECT state, missing_count, last_seen, title, url "
            "FROM opportunities WHERE id = ?",
            (manual_id,),
        ).fetchone()
        assert tuple(manual_after_empty) == tuple(manual_before)

        automatic = connection.execute(
            "SELECT id, state, missing_count "
            "FROM opportunities "
            "WHERE firm_id = ? AND fingerprint NOT LIKE 'manual:%'",
            (firm_id,),
        ).fetchone()
        assert automatic["missing_count"] == 3
        assert automatic["state"] == "Closed"

        for status in ("failed", "blocked"):
            apply_check_result(
                connection,
                firm_id,
                AdapterResult(status, (), (), "source unavailable", 403),
                started_at=STARTED,
                finished_at="2026-11-20T10:06:00",
            )
            manual_after_status = connection.execute(
                "SELECT state, missing_count, last_seen, title, url "
                "FROM opportunities WHERE id = ?",
                (manual_id,),
            ).fetchone()
            assert tuple(manual_after_status) == tuple(manual_before)
    finally:
        connection.close()
