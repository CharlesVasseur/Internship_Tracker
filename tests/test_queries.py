"""Offline tests for the read-only firm queries."""

from tracker.db import connect, init_db
from tracker.models import APPLICATION_STAGES
from tracker.queries import get_filter_options, list_firms


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
