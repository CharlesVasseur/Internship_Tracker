"""Read the firm list (step T4.1).

The input is a CSV file with one header row and one firm per row:

    firm;careers_entry_url;listing_url;example_job_url;example_job_title;terms_url

Rules (all covered by tests):

* UTF-8, a leading BOM is fine, separator ";". A file that cannot be decoded as
  UTF-8 stops the run: a wrong decoding would silently change firm names.
* `firm` and `careers_entry_url` are required columns. `listing_url`,
  `example_job_url`, `terms_url` and `api_url` are optional. Any other column
  (for example `example_job_title`) is ignored. Header names are matched
  without capitals and without surrounding spaces.
* Values are trimmed. An empty optional value means "not given" (None).
* A bad ROW never raises (spec: inputs are never validated). An empty or
  unusable careers URL is kept as written, so the fetcher later reports
  failed/invalid_url for that firm. A row without a name is called "(row N)"
  (N is its line number), so its result can still be found and resumed.
* A wholly empty row is skipped, and the reader says so in `warnings`.
* A problem with the FILE itself (missing, not UTF-8, no header, a required
  column missing, a header that looks comma-separated, a CSV syntax error)
  raises InputFileError, before any firm is contacted. This is not a firm
  result: there is no firm to attach it to.

Warnings are plain sentences for the person running the tool. They never stop
the run: a name containing "?" (accents lost in an old export), a duplicate
firm, a skipped blank row.

Pure Python standard library. No network access in this file.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from tracker.triage.contract import FirmInput

DELIMITER = ";"
REQUIRED_COLUMNS = ("firm", "careers_entry_url")
OPTIONAL_COLUMNS = ("listing_url", "example_job_url", "terms_url", "api_url")


class InputFileError(Exception):
    """The firm list itself cannot be used. The run stops before contacting any firm."""


@dataclass(frozen=True)
class FirmList:
    firms: tuple[FirmInput, ...]
    warnings: tuple[str, ...]


def read_firms(path: str | Path) -> FirmList:
    """Read a firm list from a file. Raises InputFileError if the file cannot be used."""
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise InputFileError(f"cannot read the firm list {str(path)!r}: {error.strerror or error}") from error
    try:
        text = data.decode("utf-8-sig")        # strict: a bad byte is an error, never a "?"
    except UnicodeDecodeError as error:
        raise InputFileError(
            f"the firm list is not valid UTF-8 (byte {error.start}): re-export it as CSV UTF-8") from error
    return parse_firms(text)


def parse_firms(text: str) -> FirmList:
    """Read a firm list from text. Same rules as read_firms, without the file."""
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=DELIMITER)
    try:
        header = next(reader, None)
    except csv.Error as error:
        raise InputFileError(f"line 1: not readable as CSV: {error}") from error
    if header is None or not any(cell.strip() for cell in header):
        raise InputFileError("the firm list is empty: no header row")

    names = [cell.strip().lower() for cell in header]
    if len(names) == 1 and "," in names[0]:
        raise InputFileError(
            f"the header looks comma-separated; this tool reads '{DELIMITER}' (semicolon) files")
    missing = [column for column in REQUIRED_COLUMNS if column not in names]
    if missing:
        raise InputFileError(f"required column(s) missing from the header: {', '.join(missing)}")
    index = {name: position for position, name in reversed(list(enumerate(names)))}   # first one wins

    firms: list[FirmInput] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    try:
        for row in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in row):
                warnings.append(f"line {line}: blank row skipped")
                continue

            def cell(column: str) -> str:
                position = index.get(column)
                return row[position].strip() if position is not None and position < len(row) else ""

            def optional(column: str) -> str | None:
                return cell(column) or None

            name = cell("firm") or f"(row {line})"
            careers_url = cell("careers_entry_url")
            if "?" in name:
                warnings.append(
                    f"line {line}: the name {name!r} contains '?': accents may have been lost in the export")
            key = (name, careers_url)
            if key in seen:
                warnings.append(f"line {line}: {name!r} appears more than once with the same careers URL")
            seen.add(key)
            firms.append(FirmInput(
                name=name, careers_url=careers_url,
                listing_url=optional("listing_url"), example_job_url=optional("example_job_url"),
                api_url=optional("api_url"), terms_url=optional("terms_url"),
            ))
    except csv.Error as error:
        raise InputFileError(f"line {reader.line_num}: not readable as CSV: {error}") from error

    if not firms:
        raise InputFileError("the firm list has a header but no firm rows")
    return FirmList(tuple(firms), tuple(warnings))
