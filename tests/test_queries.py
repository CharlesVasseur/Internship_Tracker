"""Offline tests for the read-only firm queries."""

import pytest

from tracker.db import connect, init_db
from tracker.models import APPLICATION_STAGES
from tracker.queries import (
    count_by_stage,
    get_firm,
    get_filter_options,
    list_applications,
    list_activities,
    list_firms,
    list_next_actions,
    list_opportunities,
    list_other_offices,
    list_pipeline,
)


def seed_database(database_path):
    init_db(database_path)
    connection = connect(database_path)
    connection.execute(
        """
        INSERT INTO firms (
            profile, name, employer_key, country, city, priority,
            category, actuarial_depth, opening_window, careers_url,
            source_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "profile",
            "Example Firm",
            "example firm",
            "France",
            "Paris",
            "P1",
            "Insurance",
            "Core",
            "Spring",
            "https://example.test/example",
            "search_link",
        ),
    )
    example_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    connection.execute(
        """
        INSERT INTO firms (
            profile, name, employer_key, country, city, priority,
            category, actuarial_depth
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "profile",
            "Sample Re",
            "sample re",
            "Switzerland",
            "Zurich",
            "P2",
            "Reinsurance",
            "Core",
        ),
    )
    sample_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    connection.execute(
        """
        INSERT INTO firms (
            profile, name, employer_key, country, city, priority,
            category, actuarial_depth
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "profile",
            "Test Pension",
            "test pension",
            "Spain",
            "Madrid",
            None,
            "Pensions",
            "Core",
        ),
    )
    pension_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    connection.execute(
        "INSERT INTO applications (firm_id, stage) VALUES (?, ?)",
        (example_id, "To research"),
    )
    connection.execute(
        "INSERT INTO applications (firm_id, stage) VALUES (?, ?)",
        (example_id, "Contacted"),
    )
    connection.execute(
        "INSERT INTO applications (firm_id, stage) VALUES (?, ?)",
        (sample_id, "Applied"),
    )
    connection.commit()
    return connection, example_id, pension_id


def test_no_filters_returns_all_firms(tmp_path):
    connection, _, _ = seed_database(tmp_path / "tracker.db")
    try:
        assert len(list_firms(connection)) == 3
    finally:
        connection.close()


def test_filters_by_country(tmp_path):
    connection, _, _ = seed_database(tmp_path / "tracker.db")
    try:
        rows = list_firms(connection, countries=["Switzerland"])
        assert [row["name"] for row in rows] == ["Sample Re"]
    finally:
        connection.close()


def test_filters_by_priority(tmp_path):
    connection, _, _ = seed_database(tmp_path / "tracker.db")
    try:
        rows = list_firms(connection, priorities=["P1"])
        assert [row["name"] for row in rows] == ["Example Firm"]
    finally:
        connection.close()


def test_filters_by_stage(tmp_path):
    connection, _, _ = seed_database(tmp_path / "tracker.db")
    try:
        rows = list_firms(connection, stages=["Contacted"])
        assert [row["name"] for row in rows] == ["Example Firm"]
    finally:
        connection.close()


def test_search_is_case_insensitive_for_name_and_employer_key(tmp_path):
    connection, _, _ = seed_database(tmp_path / "tracker.db")
    try:
        assert [row["name"] for row in list_firms(connection, search="eXAMple")] == [
            "Example Firm"
        ]
        assert [row["name"] for row in list_firms(connection, search="SAMPLE")] == [
            "Sample Re"
        ]
    finally:
        connection.close()


def test_latest_application_stage_and_application_free_firm(tmp_path):
    connection, _, pension_id = seed_database(tmp_path / "tracker.db")
    try:
        rows = list_firms(connection)
        example = next(row for row in rows if row["firm_id"] != pension_id and row["name"] == "Example Firm")
        pension = next(row for row in rows if row["firm_id"] == pension_id)
        assert example["stage"] == "Contacted"
        assert pension["stage"] is None
        assert pension["next_action_date"] is None
    finally:
        connection.close()


def test_priority_sort_order(tmp_path):
    connection, _, _ = seed_database(tmp_path / "tracker.db")
    try:
        rows = list_firms(connection)
        assert [row["name"] for row in rows] == [
            "Example Firm",
            "Sample Re",
            "Test Pension",
        ]
    finally:
        connection.close()


def test_filter_options_include_all_stages(tmp_path):
    connection, _, _ = seed_database(tmp_path / "tracker.db")
    try:
        options = get_filter_options(connection)
        assert options["stages"] == list(APPLICATION_STAGES)
    finally:
        connection.close()


def test_application_counts_are_zero_one_and_two(tmp_path):
    connection, _, pension_id = seed_database(tmp_path / "tracker.db")
    try:
        rows = list_firms(connection)
        counts = {row["name"]: row["n_applications"] for row in rows}
        assert counts == {
            "Example Firm": 2,
            "Sample Re": 1,
            "Test Pension": 0,
        }
    finally:
        connection.close()


def test_count_by_stage_returns_all_stages_in_pipeline_order(tmp_path):
    connection, _, _ = seed_database(tmp_path / "tracker.db")
    try:
        counts = count_by_stage(connection)
        assert counts == [
            ("To research", 1),
            ("Contacted", 1),
            ("Applied", 1),
            ("Online assessment", 0),
            ("Online interview", 0),
            ("In-person interview", 0),
            ("Offer", 0),
            ("Rejected", 0),
        ]
    finally:
        connection.close()


def test_list_pipeline_filters_sorts_and_includes_rejected_only_when_selected(tmp_path):
    init_db(tmp_path / "tracker.db")
    connection = connect(tmp_path / "tracker.db")
    try:
        firms = [
            ("Example Firm", "France", "Paris", "P1"),
            ("Sample Re", "Switzerland", "Zurich", "P2"),
            ("Test Pension", "Spain", "Madrid", "P3"),
        ]
        firm_ids = {}
        for name, country, city, priority in firms:
            connection.execute(
                """
                INSERT INTO firms (
                    profile, name, employer_key, country, city, priority
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("profile", name, name.lower(), country, city, priority),
            )
            firm_ids[name] = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]

        applications = [
            ("Example Firm", "Contacted", "Review", "2026-12-02", "2026-11-20"),
            ("Sample Re", "Contacted", "Call", None, "2026-11-21"),
            ("Test Pension", "Applied", "Submit", "2026-12-01", "2026-11-22"),
            ("Test Pension", "Contacted", "Confirm", "2026-12-02", "2026-11-24"),
            ("Example Firm", "Rejected", "Archive", None, "2026-11-23"),
            ("Test Pension", "To research", "Research", "2026-12-03", None),
        ]
        for name, stage, next_action, next_action_date, submitted_date in applications:
            connection.execute(
                """
                INSERT INTO applications (
                    firm_id, stage, next_action, next_action_date, submitted_date
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    firm_ids[name],
                    stage,
                    next_action,
                    next_action_date,
                    submitted_date,
                ),
            )
        connection.commit()

        assert list_pipeline(connection, []) == []

        rows = list_pipeline(connection, ["Contacted", "Applied"])
        assert [row["firm_name"] for row in rows] == [
            "Example Firm",
            "Test Pension",
            "Sample Re",
            "Test Pension",
        ]
        assert all(row["stage"] != "Rejected" for row in rows)
        assert all(row["stage"] != "To research" for row in rows)
        assert [row["next_action_date"] for row in rows[:3]] == [
            "2026-12-02",
            "2026-12-02",
            None,
        ]
        assert [row["next_action_date"] for row in rows] == [
            "2026-12-02",
            "2026-12-02",
            None,
            "2026-12-01",
        ]

        rejected_rows = list_pipeline(connection, ["Rejected"])
        assert [row["firm_name"] for row in rejected_rows] == ["Example Firm"]
        assert rejected_rows[0]["stage"] == "Rejected"
        assert rejected_rows[0]["submitted_date"] == "2026-11-23"

        pipeline_rows = list_pipeline(
            connection,
            ["To research", "Contacted", "Applied", "Rejected"],
        )
        assert [row["stage"] for row in pipeline_rows] == [
            "To research",
            "Contacted",
            "Contacted",
            "Contacted",
            "Applied",
            "Rejected",
        ]
    finally:
        connection.close()


def test_get_firm_returns_row_or_none(tmp_path):
    connection, example_id, pension_id = seed_database(tmp_path / "tracker.db")
    try:
        firm = get_firm(connection, example_id)
        assert firm is not None
        assert firm["name"] == "Example Firm"
        assert firm["country"] == "France"
        assert get_firm(connection, -1) is None
    finally:
        connection.close()


def test_list_applications_returns_only_firm_newest_first(tmp_path):
    connection, example_id, pension_id = seed_database(tmp_path / "tracker.db")
    try:
        applications = list_applications(connection, example_id)
        assert [application["stage"] for application in applications] == [
            "Contacted",
            "To research",
        ]
        assert list_applications(connection, pension_id) == []
    finally:
        connection.close()


def test_list_opportunities_returns_none_then_orders_last_seen(tmp_path):
    connection, example_id, pension_id = seed_database(tmp_path / "tracker.db")
    try:
        assert list_opportunities(connection, pension_id) == []
        connection.execute(
            """
            INSERT INTO opportunities (
                firm_id, title, location, url, state, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                example_id,
                "Recent role",
                "Paris",
                "https://example.test/recent",
                "Open",
                "2026-01-01",
                "2026-11-18",
            ),
        )
        connection.execute(
            """
            INSERT INTO opportunities (
                firm_id, title, location, url, state, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                example_id,
                "Older role",
                "Paris",
                "https://example.test/older",
                "Closed",
                "2025-01-01",
                "2025-11-18",
            ),
        )
        connection.execute(
            """
            INSERT INTO opportunities (
                firm_id, title, location, url, state, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                example_id,
                "Unknown date role",
                "Paris",
                "https://example.test/unknown",
                "Needs review",
                "2026-01-01",
                None,
            ),
        )
        connection.commit()
        opportunities = list_opportunities(connection, example_id)
        assert [opportunity["title"] for opportunity in opportunities] == [
            "Recent role",
            "Older role",
            "Unknown date role",
        ]
    finally:
        connection.close()


def test_list_other_offices_matches_profile_and_employer(tmp_path):
    connection, example_id, pension_id = seed_database(tmp_path / "tracker.db")
    try:
        connection.execute(
            """
            INSERT INTO firms (
                profile, name, employer_key, country, city
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("profile", "Example Firm", "example firm", "Spain", "Madrid"),
        )
        same_office_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.execute(
            """
            INSERT INTO firms (
                profile, name, employer_key, country, city
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("other-profile", "Example Firm", "example firm", "Portugal", "Lisbon"),
        )
        connection.execute(
            """
            INSERT INTO firms (
                profile, name, employer_key, country, city
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("profile", "Test Pension", None, "France", "Paris"),
        )
        null_key_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.commit()

        offices = list_other_offices(connection, example_id)
        assert [office["firm_id"] for office in offices] == [same_office_id]
        assert offices[0]["country"] == "Spain"
        assert list_other_offices(connection, pension_id) == []
        assert list_other_offices(connection, null_key_id) == []
    finally:
        connection.close()


def test_list_activities_newest_first(tmp_path):
    connection, example_id, pension_id = seed_database(tmp_path / "tracker.db")
    try:
        application_id = connection.execute(
            "SELECT id FROM applications WHERE firm_id = ?",
            (example_id,),
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO activities (application_id, type, date, notes) "
            "VALUES (?, 'note', '2026-11-19', 'Older')",
            (application_id,),
        )
        connection.execute(
            "INSERT INTO activities (application_id, type, date, notes) "
            "VALUES (?, 'note', '2026-11-20', 'Newest')",
            (application_id,),
        )
        connection.execute(
            "INSERT INTO activities (application_id, type, date, notes) "
            "VALUES (?, 'note', '2026-11-20', 'Same date newest id')",
            (application_id,),
        )
        connection.commit()
        activities = list_activities(connection, application_id)
        assert [activity["notes"] for activity in activities[:2]] == [
            "Same date newest id",
            "Newest",
        ]
    finally:
        connection.close()


def test_stage_filter_finds_older_application(tmp_path):
    connection, example_id, pension_id = seed_database(tmp_path / "tracker.db")
    try:
        applications = connection.execute(
            "SELECT id FROM applications WHERE firm_id = ? ORDER BY id",
            (example_id,),
        ).fetchall()
        older_id = applications[0]["id"]
        newer_id = applications[1]["id"]
        connection.execute(
            "UPDATE applications SET stage = 'Contacted' WHERE id = ?",
            (older_id,),
        )
        connection.execute(
            "UPDATE applications SET stage = 'Applied' WHERE id = ?",
            (newer_id,),
        )
        connection.commit()
        rows = list_firms(connection, stages=["Contacted"])
        names = [row["name"] for row in rows]
        assert "Example Firm" in names
    finally:
        connection.close()


def test_list_next_actions_groups_dates_exclusions_and_sorting(tmp_path):
    init_db(tmp_path / "tracker.db")
    connection = connect(tmp_path / "tracker.db")
    try:
        firms = [
            ("Example Firm", "France", "Paris"),
            ("Sample Re", "Switzerland", "Zurich"),
            ("Test Pension", "Spain", "Madrid"),
        ]
        firm_ids = {}
        for name, country, city in firms:
            connection.execute(
                """
                INSERT INTO firms (
                    profile, name, employer_key, country, city
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("profile", name, name.lower(), country, city),
            )
            firm_ids[name] = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]

        applications = [
            ("Example Firm", "Overdue old", "2026-11-29", "Applied"),
            ("Sample Re", "Overdue newer", "2026-11-30", "Contacted"),
            ("Test Pension", "Due today", "2026-12-01", "Offer"),
            ("Example Firm", "Due tomorrow", "2026-12-02", "Applied"),
            ("Sample Re", "Due horizon", "2026-12-08", "Online interview"),
            ("Test Pension", "Beyond horizon", "2026-12-09", "Applied"),
            ("Example Firm", "Rejected action", "2026-12-01", "Rejected"),
            ("Sample Re", "No date action", None, "Applied"),
            ("Test Pension", "Unreadable slash", "01/12/2026", "Applied"),
            ("Example Firm", "Unreadable text", "tomorrow", "Contacted"),
            ("Test Pension", None, None, "Applied"),
        ]
        for name, next_action, next_action_date, stage in applications:
            connection.execute(
                """
                INSERT INTO applications (
                    firm_id, stage, next_action, next_action_date
                ) VALUES (?, ?, ?, ?)
                """,
                (firm_ids[name], stage, next_action, next_action_date),
            )
        connection.commit()

        result = list_next_actions(connection, "2026-12-01")

        assert [item["next_action"] for item in result["overdue"]] == [
            "Overdue old",
            "Overdue newer",
        ]
        assert [item["next_action"] for item in result["today"]] == [
            "Due today",
        ]
        assert [item["next_action"] for item in result["upcoming"]] == [
            "Due tomorrow",
            "Due horizon",
        ]
        assert all(
            item["next_action"] != "Beyond horizon"
            for items in result.values()
            for item in items
        )
        assert all(
            item["next_action"] != "Rejected action"
            for items in result.values()
            for item in items
        )
        assert [item["next_action"] for item in result["no_date"]] == [
            "No date action",
        ]
        assert [item["next_action"] for item in result["unreadable"]] == [
            "Unreadable text",
            "Unreadable slash",
        ]
        assert [item["firm_name"] for item in result["unreadable"]] == [
            "Example Firm",
            "Test Pension",
        ]
        assert all(
            item["next_action"] != "None"
            for items in result.values()
            for item in items
        )
        assert all(
            item["next_action"] != None
            for item in result["no_date"]
        )
        assert result["today"][0]["stage"] == "Offer"
        assert all(
            item["next_action"] != "None"
            for items in result.values()
            for item in items
        )
    finally:
        connection.close()


def test_list_next_actions_sorting_by_date_then_firm_name(tmp_path):
    init_db(tmp_path / "tracker.db")
    connection = connect(tmp_path / "tracker.db")
    try:
        for name, city in (
            ("Test Pension", "Madrid"),
            ("Example Firm", "Paris"),
            ("Sample Re", "Zurich"),
        ):
            connection.execute(
                """
                INSERT INTO firms (
                    profile, name, employer_key, country, city
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("profile", name, name.lower(), "France", city),
            )
            firm_id = connection.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
            date_value = {
                "Test Pension": "2026-11-30",
                "Example Firm": "2026-11-29",
                "Sample Re": "2026-11-30",
            }[name]
            connection.execute(
                """
                INSERT INTO applications (
                    firm_id, stage, next_action, next_action_date
                ) VALUES (?, 'Applied', 'Act', ?)
                """,
                (firm_id, date_value),
            )
        connection.commit()

        result = list_next_actions(connection, "2026-12-01")
        assert [item["firm_name"] for item in result["overdue"]] == [
            "Example Firm",
            "Sample Re",
            "Test Pension",
        ]
    finally:
        connection.close()


def test_list_next_actions_rejects_invalid_today(tmp_path):
    init_db(tmp_path / "tracker.db")
    connection = connect(tmp_path / "tracker.db")
    try:
        with pytest.raises(ValueError):
            list_next_actions(connection, "01/12/2026")
    finally:
        connection.close()
