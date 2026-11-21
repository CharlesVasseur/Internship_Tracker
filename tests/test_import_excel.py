"""Offline tests for importing the tracker workbook."""

from datetime import datetime

from openpyxl import Workbook

from scripts.import_excel import main
from tracker.db import connect, init_db
from tracker.importer import import_excel, normalize


HEADERS = [
    "#",
    "Company",
    "Country",
    "City",
    "Category",
    "Actuarial depth",
    "Role types",
    "Internship type",
    "Eligibility",
    "Priority",
    "Language fit",
    "Opening window",
    "Application link / contact",
    "Work authorisation (CH only)",
    "Notes",
    "Last verified",
    "Status",
    "Next action date",
    "Points",
]


def build_workbook(
    path,
    *,
    remove_header=None,
    unknown_status=False,
    invalid_priority=None,
    empty_priority=False,
    empty_country=False,
    duplicate=False,
):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Tracker"
    headers = [header for header in HEADERS if header != remove_header]
    sheet.append(headers)
    rows = [
        ("Example Firm", "France", "Paris", "Careers search", "To research", "P1"),
        ("Example Firm", "Spain", "Madrid", "Careers search", "Contacted", "P2"),
        ("Sample Re", "France", "Lyon", "Direct posting", "Applied", "P3"),
        ("Test Pension", "Switzerland", "Zurich", "Careers search", "Offer", "P4"),
    ]
    if duplicate:
        rows[1] = ("Example Firm", "France", "Paris", "Careers search", "Contacted", "P2")
    if invalid_priority is not None:
        rows[0] = (
            "Example Firm",
            "France",
            "Paris",
            "Careers search",
            "To research",
            invalid_priority,
        )
    if empty_priority:
        rows[0] = (
            "Example Firm",
            "France",
            "Paris",
            "Careers search",
            "To research",
            "",
        )
    if empty_country:
        rows[0] = (
            "Example Firm",
            "",
            "Paris",
            "Careers search",
            "To research",
            "P1",
        )
    for row_number, row in enumerate(rows, start=2):
        company, country, city, link_text, status, priority = row
        values = {
            "Company": company,
            "Country": country,
            "City": city,
            "Category": "Example",
            "Actuarial depth": "Core",
            "Role types": "Life and Pensions",
            "Internship type": "Summer",
            "Eligibility": "Student",
            "Priority": priority,
            "Language fit": "English",
            "Opening window": "Spring",
            "Application link / contact": link_text,
            "Work authorisation (CH only)": "",
            "Notes": "Synthetic note",
            "Last verified": (
                datetime(2026, 11, 19) if row_number == 2 else ""
            ),
            "Status": "Unknown" if unknown_status and row_number == 2 else status,
            "Next action date": (
                datetime(2026, 12, 1) if row_number == 2 else ""
            ),
            "#": row_number - 1,
            "Points": f"={row_number}*10",
        }
        sheet.append([values[header] for header in headers])
        link_column = headers.index("Application link / contact") + 1
        sheet.cell(row_number, link_column).hyperlink = (
            f"https://example.test/{row_number}"
        )
    workbook.save(path)


def test_imports_firms_applications_and_is_idempotent(tmp_path):
    workbook_path = tmp_path / "tracker.xlsx"
    database_path = tmp_path / "tracker.db"
    build_workbook(workbook_path)

    first = import_excel(workbook_path, database_path, "profile")
    second = import_excel(workbook_path, database_path, "profile")

    assert first["rows_read"] == 4
    assert first["firms_inserted"] == 4
    assert first["applications_created"] == 4
    assert second["firms_inserted"] == 0
    assert second["firms_skipped"] == 4
    assert second["applications_created"] == 0
    assert first["distinct_employer_keys"] == 3

    with connect(database_path) as connection:
        firms = connection.execute(
            """
            SELECT name, employer_key, country, city, category, actuarial_depth,
                   role_types, priority, careers_url, source_type, eligibility,
                   internship_type, language_fit, opening_window,
                   work_authorisation, notes, last_verified, monitoring
            FROM firms ORDER BY id
            """
        ).fetchall()
        applications = connection.execute(
            "SELECT stage, next_action_date FROM applications ORDER BY id"
        ).fetchall()
        first = firms[0]
        assert first["name"] == "Example Firm"
        assert first["employer_key"] == "example firm"
        assert first["country"] == "France"
        assert first["city"] == "Paris"
        assert first["category"] == "Example"
        assert first["actuarial_depth"] == "Core"
        assert first["role_types"] == "Life and Pensions"
        assert first["priority"] == "P1"
        assert first["careers_url"] == "https://example.test/2"
        assert first["careers_url"] != "Careers search"
        assert first["source_type"] == "search_link"
        assert first["eligibility"] == "Student"
        assert first["internship_type"] == "Summer"
        assert first["language_fit"] == "English"
        assert first["opening_window"] == "Spring"
        assert first["work_authorisation"] is None
        assert first["notes"] == "Synthetic note"
        assert first["last_verified"] == "2026-11-19"
        assert first["monitoring"] == "manual"
        assert {row["priority"] for row in firms} >= {"P1", "P2", "P3", "P4"}
        assert [row["stage"] for row in applications] == [
            "To research",
            "Contacted",
            "Applied",
            "Offer",
        ]
        assert applications[0]["next_action_date"] == "2026-12-01"
        assert connection.execute("SELECT COUNT(*) FROM point_events").fetchone()[0] == 0


def test_import_without_profile_uses_default(tmp_path):
    workbook_path = tmp_path / "tracker.xlsx"
    database_path = tmp_path / "tracker.db"
    build_workbook(workbook_path)

    assert main(["--xlsx", str(workbook_path), "--db", str(database_path)]) == 0

    with connect(database_path) as connection:
        profile = connection.execute(
            "SELECT profile FROM firms ORDER BY id LIMIT 1"
        ).fetchone()["profile"]
    assert profile == "default"


def test_missing_header_returns_two_and_writes_nothing(tmp_path, capsys):
    workbook_path = tmp_path / "tracker.xlsx"
    database_path = tmp_path / "tracker.db"
    build_workbook(workbook_path, remove_header="Country")
    init_db(database_path)

    assert main(
        ["--xlsx", str(workbook_path), "--db", str(database_path), "--profile", "profile"]
    ) == 2
    assert "Country" in capsys.readouterr().err
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM firms").fetchone()[0] == 0


def test_unknown_status_returns_two_and_writes_nothing(tmp_path, capsys):
    workbook_path = tmp_path / "tracker.xlsx"
    database_path = tmp_path / "tracker.db"
    build_workbook(workbook_path, unknown_status=True)
    init_db(database_path)

    assert main(
        ["--xlsx", str(workbook_path), "--db", str(database_path), "--profile", "profile"]
    ) == 2
    assert "row 2" in capsys.readouterr().err
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM firms").fetchone()[0] == 0


def test_invalid_priority_returns_two_and_writes_nothing(tmp_path, capsys):
    workbook_path = tmp_path / "tracker.xlsx"
    database_path = tmp_path / "tracker.db"
    build_workbook(workbook_path, invalid_priority="P5")
    init_db(database_path)

    assert main(
        ["--xlsx", str(workbook_path), "--db", str(database_path), "--profile", "profile"]
    ) == 2
    error = capsys.readouterr().err
    assert "row 2" in error
    assert "Priority" in error
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM firms").fetchone()[0] == 0


def test_empty_priority_is_imported_as_null(tmp_path):
    workbook_path = tmp_path / "tracker.xlsx"
    database_path = tmp_path / "tracker.db"
    build_workbook(workbook_path, empty_priority=True)

    summary = import_excel(workbook_path, database_path, "profile")

    assert summary["firms_inserted"] == 4
    with connect(database_path) as connection:
        priority = connection.execute(
            "SELECT priority FROM firms WHERE name = 'Example Firm' AND country = 'France'"
        ).fetchone()["priority"]
    assert priority is None


def test_empty_country_returns_two_and_writes_nothing(tmp_path, capsys):
    workbook_path = tmp_path / "tracker.xlsx"
    database_path = tmp_path / "tracker.db"
    build_workbook(workbook_path, empty_country=True)
    init_db(database_path)

    assert main(
        ["--xlsx", str(workbook_path), "--db", str(database_path), "--profile", "profile"]
    ) == 2
    error = capsys.readouterr().err
    assert "row 2" in error
    assert "Country" in error
    with connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM firms").fetchone()[0] == 0


def test_duplicate_in_file_is_imported_once_with_warning(tmp_path, capsys):
    workbook_path = tmp_path / "tracker.xlsx"
    database_path = tmp_path / "tracker.db"
    build_workbook(workbook_path, duplicate=True)

    summary = import_excel(workbook_path, database_path, "profile")
    error = capsys.readouterr().err

    assert summary["firms_inserted"] == 3
    assert "row 3 duplicates row 2" in error
    assert error.count("duplicates row") == 1
    with connect(database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM firms "
            "WHERE name = 'Example Firm' AND country = 'France' AND city = 'Paris'"
        ).fetchone()[0]
    assert count == 1


def test_normalize_removes_accents_and_punctuation():
    assert normalize("Crédit  Agricole") == "credit agricole"
    assert normalize("Forvis-Mazars") == "forvis mazars"
