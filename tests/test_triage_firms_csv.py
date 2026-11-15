"""Tests for the firm-list reader (T4.1). Synthetic data only: no real firm names."""

import pytest

from tracker.triage.contract import FirmInput
from tracker.triage.firms_csv import InputFileError, parse_firms, read_firms

HEADER = "firm;careers_entry_url;listing_url;example_job_url;example_job_title;terms_url"


def make(*rows, header=HEADER):
    return "\n".join([header, *rows]) + "\n"


# -- the normal case --------------------------------------------------------


def test_a_full_row_maps_every_column():
    result = parse_firms(make(
        "Alpha;https://a.test/careers;https://a.test/jobs;https://a.test/jobs/1;Intern;https://a.test/terms"))
    assert result.firms == (FirmInput(
        name="Alpha", careers_url="https://a.test/careers", listing_url="https://a.test/jobs",
        example_job_url="https://a.test/jobs/1", api_url=None, terms_url="https://a.test/terms"),)
    assert result.warnings == ()


def test_the_job_title_column_is_ignored():
    firm = parse_firms(make("Alpha;https://a.test;;;Some Title;")).firms[0]
    assert "Some Title" not in repr(firm)


def test_several_rows_keep_their_order():
    result = parse_firms(make("A;https://a.test;;;;", "B;https://b.test;;;;", "C;https://c.test;;;;"))
    assert [firm.name for firm in result.firms] == ["A", "B", "C"]


def test_accents_survive():
    assert parse_firms(make("Société Générale;https://a.test;;;;")).firms[0].name == "Société Générale"


def test_values_are_trimmed():
    firm = parse_firms(make("  Alpha  ; https://a.test ; https://a.test/jobs ;;;")).firms[0]
    assert (firm.name, firm.careers_url, firm.listing_url) == ("Alpha", "https://a.test", "https://a.test/jobs")


def test_empty_optional_values_become_none():
    firm = parse_firms(make("Alpha;https://a.test;;;;")).firms[0]
    assert (firm.listing_url, firm.example_job_url, firm.terms_url, firm.api_url) == (None, None, None, None)


def test_windows_line_endings():
    result = parse_firms(make("A;https://a.test;;;;", "B;https://b.test;;;;").replace("\n", "\r\n"))
    assert [firm.name for firm in result.firms] == ["A", "B"]


# -- the header -------------------------------------------------------------


def test_header_names_ignore_capitals_and_spaces():
    result = parse_firms("  Firm ; CAREERS_ENTRY_URL \nAlpha;https://a.test\n")
    assert result.firms[0].careers_url == "https://a.test"


def test_columns_can_come_in_any_order():
    result = parse_firms("careers_entry_url;terms_url;firm\nhttps://a.test;https://a.test/t;Alpha\n")
    firm = result.firms[0]
    assert (firm.name, firm.careers_url, firm.terms_url) == ("Alpha", "https://a.test", "https://a.test/t")


def test_only_the_two_required_columns_are_enough():
    firm = parse_firms("firm;careers_entry_url\nAlpha;https://a.test\n").firms[0]
    assert firm.listing_url is None


def test_the_reserved_api_url_column_is_read():
    firm = parse_firms("firm;careers_entry_url;api_url\nAlpha;https://a.test;https://a.test/api\n").firms[0]
    assert firm.api_url == "https://a.test/api"


def test_a_repeated_column_uses_the_first_one():
    firm = parse_firms("firm;careers_entry_url;listing_url;listing_url\nA;https://a.test;first;second\n").firms[0]
    assert firm.listing_url == "first"


@pytest.mark.parametrize("header, missing", [
    ("careers_entry_url;listing_url", "firm"),
    ("firm;listing_url", "careers_entry_url"),
])
def test_a_missing_required_column_stops_the_run(header, missing):
    with pytest.raises(InputFileError, match=missing):
        parse_firms(header + "\nx;y\n")


def test_a_comma_separated_file_is_refused_with_a_clear_message():
    with pytest.raises(InputFileError, match="comma-separated"):
        parse_firms("firm,careers_entry_url\nAlpha,https://a.test\n")


@pytest.mark.parametrize("text", ["", "\n\n", ";;;\n"])
def test_a_file_without_a_header_is_refused(text):
    with pytest.raises(InputFileError, match="no header"):
        parse_firms(text)


def test_a_header_without_firms_is_refused():
    with pytest.raises(InputFileError, match="no firm rows"):
        parse_firms(HEADER + "\n")


# -- bad rows are results later, never exceptions -----------------------------


def test_a_row_without_a_careers_url_is_kept_as_written():
    firm = parse_firms(make("Alpha;;;;;")).firms[0]
    assert firm.careers_url == ""           # the fetcher will report failed/invalid_url


def test_a_garbage_url_is_not_validated():
    assert parse_firms(make("Alpha;not a url at all;;;;")).firms[0].careers_url == "not a url at all"


def test_a_row_without_a_name_is_called_by_its_line_number():
    result = parse_firms(make("A;https://a.test;;;;", ";https://b.test;;;;"))
    assert result.firms[1].name == "(row 3)"


def test_a_short_row_is_filled_with_nothing():
    firm = parse_firms(make("Alpha;https://a.test")).firms[0]
    assert firm.terms_url is None


def test_extra_cells_are_ignored():
    firm = parse_firms(make("Alpha;https://a.test;;;;;surplus;more")).firms[0]
    assert firm.name == "Alpha"


def test_a_blank_row_is_skipped_and_reported():
    result = parse_firms(make("A;https://a.test;;;;", ";;;;;", "B;https://b.test;;;;"))
    assert [firm.name for firm in result.firms] == ["A", "B"]
    assert result.warnings == ("line 3: blank row skipped",)


def test_a_wholly_empty_line_is_skipped_too():
    result = parse_firms(make("A;https://a.test;;;;", "", "B;https://b.test;;;;"))
    assert len(result.firms) == 2 and len(result.warnings) == 1


def test_a_quoted_cell_with_a_semicolon_stays_in_one_cell():
    firm = parse_firms(make('"Alpha; Beta";https://a.test;;;;')).firms[0]
    assert firm.name == "Alpha; Beta"


# -- warnings ------------------------------------------------------------------


def test_a_question_mark_in_a_name_warns_about_lost_accents():
    result = parse_firms(make("Soci?t? Example;https://a.test;;;;"))
    assert len(result.firms) == 1
    assert "accents may have been lost" in result.warnings[0] and result.warnings[0].startswith("line 2")


def test_a_question_mark_in_a_url_does_not_warn():
    assert parse_firms(make("Alpha;https://a.test/jobs?id=1;;;;")).warnings == ()


def test_a_duplicate_firm_warns_but_both_rows_are_kept():
    result = parse_firms(make("A;https://a.test;;;;", "A;https://a.test;;;;"))
    assert len(result.firms) == 2
    assert "more than once" in result.warnings[0]


def test_same_name_with_another_careers_url_is_not_a_duplicate():
    assert parse_firms(make("A;https://a.test;;;;", "A;https://other.test;;;;")).warnings == ()


# -- files ---------------------------------------------------------------------


def test_read_firms_reads_a_utf8_file_with_a_bom(tmp_path):
    path = tmp_path / "firms.csv"
    path.write_bytes(("\ufeff" + make("Société;https://a.test;;;;")).encode("utf-8"))
    assert read_firms(path).firms[0].name == "Société"


def test_a_missing_file_stops_the_run_with_a_clear_message(tmp_path):
    with pytest.raises(InputFileError, match="cannot read the firm list"):
        read_firms(tmp_path / "absent.csv")


def test_a_file_that_is_not_utf8_stops_the_run(tmp_path):
    path = tmp_path / "firms.csv"
    path.write_bytes(make("Soci\xe9t\xe9;https://a.test;;;;").encode("latin-1"))
    with pytest.raises(InputFileError, match="not valid UTF-8"):
        read_firms(path)


def test_a_csv_syntax_problem_names_its_line():
    huge = "x" * 200_000                        # over the csv module's field size limit
    with pytest.raises(InputFileError, match="not readable as CSV"):
        parse_firms(make(f"A;https://a.test;;;;", f"B;{huge};;;;"))


@pytest.mark.parametrize("junk", ["\x00", "\ufeff", "\t", "'\"", "<script>", "../../x", "\u202e"])
def test_odd_cell_content_never_raises(junk):
    result = parse_firms(make(f"Alpha;{junk};{junk};{junk};{junk};{junk}"))
    assert len(result.firms) == 1
