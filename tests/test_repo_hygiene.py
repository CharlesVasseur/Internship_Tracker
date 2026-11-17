"""Repository hygiene (T5.4, gap G13): the .gitignore lines the project relies on, and no firm-specific
name anywhere in src/ or tests/.

The firm-name check reads the local, git-ignored firm list (survey_input.csv) so that no firm name has
to be written into the repository to look for it. Without that file (for example on a CI machine) the
check is skipped; the scanner itself is tested here with invented names.
"""

import re
from pathlib import Path

import pytest

from tracker.triage.firms_csv import InputFileError, read_firms

ROOT = Path(__file__).resolve().parent.parent
SCANNED_FOLDERS = ("src", "tests")
SCANNED_SUFFIXES = {".py", ".txt", ".html", ".xml", ".json", ".md", ".csv", ".jsonl"}
SKIPPED_PARTS = {"local_fixtures", "__pycache__", ".pytest_cache"}
REQUIRED_IGNORED = ["triage_cache/", "tests/local_fixtures/*", "!tests/local_fixtures/README.md",
                    "triage_output*.jsonl", "survey_input.csv"]


# -- the scanner -----------------------------------------------------------------------------------------------------


def patterns_for(name: str) -> list[re.Pattern]:
    """Regexes for a firm name: the whole name, and its first words when that is still distinctive.

    A "?" in the name (an accent lost in an old export) matches any one letter. Names of 4 characters
    or fewer are matched with their capitals, so a short name does not match ordinary words.
    """
    words = name.split()
    variants = [name] + [" ".join(words[:count]) for count in range(len(words) - 1, 1, -1)
                         if len(" ".join(words[:count])) >= 12]
    flags = re.IGNORECASE if len(name) > 4 else 0
    return [re.compile(r"(?<!\w)" + re.escape(variant).replace(r"\?", r"\w") + r"(?!\w)", flags) for variant in variants]


def find_names(names: list[str], roots: list[Path]) -> list[str]:
    """'path: name' for every scanned file that contains one of the names."""
    compiled = [(name, patterns_for(name)) for name in names if name.strip()]
    hits = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SCANNED_SUFFIXES or SKIPPED_PARTS & set(path.parts):
                continue
            text = path.read_bytes().decode("utf-8", errors="ignore")
            for name, regexes in compiled:
                if any(regex.search(text) for regex in regexes):
                    hits.append(f"{path.relative_to(root.parent) if root.parent in path.parents else path}: {name}")
    return hits


# -- the scanner, tested with invented names ------------------------------------------------------------------------------


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests" / "local_fixtures").mkdir(parents=True)
    return tmp_path


def test_the_scanner_finds_a_whole_name_in_any_scanned_file(tree):
    (tree / "src" / "a.py").write_text("# Zorblax Holdings\n", encoding="utf-8")
    (tree / "tests" / "page.html").write_text("<p>Welcome to ZORBLAX HOLDINGS</p>", encoding="utf-8")
    hits = find_names(["Zorblax Holdings"], [tree / "src", tree / "tests"])
    assert len(hits) == 2


def test_the_scanner_finds_a_shortened_name_and_an_accent_lost_in_the_export(tree):
    (tree / "src" / "a.py").write_text("# Crédit Zorblax is mentioned here\n# Quuxland Financial\n", encoding="utf-8")
    assert find_names(["Cr?dit Zorblax Assurances"], [tree / "src"])
    assert find_names(["Quuxland Financial Services"], [tree / "src"])


def test_a_very_short_first_word_group_is_not_enough_for_a_match(tree):
    (tree / "src" / "a.py").write_text("# Banque de ...\n", encoding="utf-8")
    assert find_names(["Banque de Zorblaxie"], [tree / "src"]) == []


def test_short_names_are_matched_with_their_capitals_only(tree):
    (tree / "src" / "a.py").write_text("they asked: zx or ZX?\nhey\n", encoding="utf-8")
    assert len(find_names(["ZX"], [tree / "src"])) == 1                 # the capitals match, "zx" and "hey" do not
    (tree / "src" / "a.py").write_text("they asked about zx only\n", encoding="utf-8")
    assert find_names(["ZX"], [tree / "src"]) == []


def test_a_name_inside_another_word_is_not_a_match(tree):
    (tree / "src" / "a.py").write_text("Zorblaxes and Zorblax2\n", encoding="utf-8")
    assert find_names(["Zorblax"], [tree / "src"]) == []


def test_the_local_fixtures_folder_and_other_file_types_are_not_scanned(tree):
    (tree / "tests" / "local_fixtures" / "real.html").write_text("Zorblax Holdings", encoding="utf-8")
    (tree / "src" / "image.png").write_bytes(b"Zorblax Holdings")
    assert find_names(["Zorblax Holdings"], [tree / "src", tree / "tests"]) == []


# -- the repository itself --------------------------------------------------------------------------------------------------


def test_gitignore_has_the_lines_the_project_relies_on_and_none_is_indented():
    path = ROOT / ".gitignore"
    assert path.exists(), ".gitignore is missing"
    lines = path.read_bytes().decode("utf-8").splitlines()
    indented = [line for line in lines if line[:1] in (" ", "\t")]
    assert not indented, f"git does not trim leading spaces, so these lines do nothing: {indented}"
    missing = [entry for entry in REQUIRED_IGNORED if entry not in lines]
    assert not missing, f"add these lines to .gitignore: {missing}"


def test_no_firm_name_from_the_local_firm_list_appears_in_src_or_tests():
    firm_list = ROOT / "survey_input.csv"
    if not firm_list.exists():
        pytest.skip("survey_input.csv is not on this machine")
    try:
        names = [firm.name for firm in read_firms(firm_list).firms]
    except InputFileError as error:
        pytest.skip(f"survey_input.csv cannot be read: {error}")
    hits = find_names(names, [ROOT / folder for folder in SCANNED_FOLDERS])
    assert hits == [], "firm-specific text in the repository:\n  " + "\n  ".join(hits)
