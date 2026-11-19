"""Import tracker firms and applications from an offline Excel workbook."""

from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
import re
import sys
import unicodedata
import warnings

from openpyxl import load_workbook

from tracker.db import connect, init_db
from tracker.models import APPLICATION_STAGES


REQUIRED_HEADERS = (
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
)


class ImportValidationError(ValueError):
    """A workbook cannot be imported without writing partial data."""


def normalize(name: str) -> str:
    """Create a stable employer key from a company name."""
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    without_accents = "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", without_accents).strip()


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _nullable_text(value: object) -> str | None:
    text = _text(value)
    return text or None


def _date_text(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _load_rows(path: str | Path) -> tuple[list[dict[str, object]], list[str]]:
    """Load and validate workbook headers before any database is opened."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        workbook = load_workbook(path, read_only=False)

    try:
        if "Tracker" not in workbook.sheetnames:
            raise ImportValidationError("missing sheet: Tracker")
        sheet = workbook["Tracker"]
        header_values = [cell.value for cell in sheet[1]]
        headers = {
            str(value).strip(): index
            for index, value in enumerate(header_values)
            if value is not None
        }
        missing = [header for header in REQUIRED_HEADERS if header not in headers]
        if missing:
            raise ImportValidationError(
                "missing required header(s): " + ", ".join(missing)
            )

        rows = []
        warnings_to_print = []
        seen_firm_rows: dict[tuple[str, str, str], int] = {}
        for row_number, cells in enumerate(
            sheet.iter_rows(min_row=2), start=2
        ):
            values = {
                header: cells[index].value if index < len(cells) else None
                for header, index in headers.items()
            }
            company = _text(values["Company"])
            if not company:
                continue
            country = _text(values["Country"])
            if not country:
                raise ImportValidationError(
                    f"row {row_number}: Country must not be empty"
                )
            priority = _nullable_text(values["Priority"])
            if priority is not None and priority not in ("P1", "P2", "P3", "P4"):
                raise ImportValidationError(
                    f"row {row_number}: invalid Priority {priority!r}"
                )
            status = _text(values["Status"])
            if status not in APPLICATION_STAGES:
                raise ImportValidationError(
                    f"row {row_number}: unknown Status {status!r}"
                )
            city = _text(values["City"])
            identity = (company, country, city)
            if identity in seen_firm_rows:
                first_row = seen_firm_rows[identity]
                warnings_to_print.append(
                    f"row {row_number} duplicates row {first_row}, "
                    "it will be skipped"
                )
            else:
                seen_firm_rows[identity] = row_number
            link_cell = cells[headers["Application link / contact"]]
            hyperlink = link_cell.hyperlink
            careers_url = hyperlink.target if hyperlink else None
            if careers_url is None:
                warnings_to_print.append(
                    f"row {row_number}: Application link / contact has no hyperlink"
                )
            link_text = _text(link_cell.value)
            if link_text == "Careers search":
                source_type = "search_link"
            elif link_text == "Direct posting":
                source_type = "direct_posting"
            else:
                source_type = None
                warnings_to_print.append(
                    f"row {row_number}: unknown Application link / contact text"
                )
            rows.append(
                {
                    "row_number": row_number,
                    "name": company,
                    "employer_key": normalize(company),
                    "country": country,
                    "city": city,
                    "category": _nullable_text(values["Category"]),
                    "actuarial_depth": _nullable_text(values["Actuarial depth"]),
                    "role_types": _nullable_text(values["Role types"]),
                    "internship_type": _nullable_text(values["Internship type"]),
                    "eligibility": _nullable_text(values["Eligibility"]),
                    "priority": priority,
                    "language_fit": _nullable_text(values["Language fit"]),
                    "opening_window": _nullable_text(values["Opening window"]),
                    "careers_url": careers_url,
                    "source_type": source_type,
                    "work_authorisation": _nullable_text(
                        values["Work authorisation (CH only)"]
                    ),
                    "notes": _nullable_text(values["Notes"]),
                    "last_verified": _date_text(values["Last verified"]),
                    "stage": status,
                    "next_action_date": _date_text(values["Next action date"]),
                }
            )
        return rows, warnings_to_print
    finally:
        workbook.close()


def import_excel(
    xlsx_path: str | Path,
    db_path: str | Path,
    profile: str,
) -> dict[str, object]:
    """Import valid workbook rows atomically and return a summary."""
    rows, warnings_to_print = _load_rows(xlsx_path)
    for warning in warnings_to_print:
        print(f"Warning: {warning}", file=sys.stderr)

    init_db(db_path)
    inserted = 0
    skipped = 0
    applications_created = 0
    country_counts: Counter[str] = Counter()

    with closing(connect(db_path)) as connection:
        try:
            for row in rows:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO firms (
                        profile, name, employer_key, country, city, category,
                        actuarial_depth, role_types, priority, careers_url,
                        source_type, monitoring, eligibility, internship_type,
                        language_fit, opening_window, work_authorisation, notes,
                        last_verified
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile,
                        row["name"],
                        row["employer_key"],
                        row["country"],
                        row["city"],
                        row["category"],
                        row["actuarial_depth"],
                        row["role_types"],
                        row["priority"],
                        row["careers_url"],
                        row["source_type"],
                        row["eligibility"],
                        row["internship_type"],
                        row["language_fit"],
                        row["opening_window"],
                        row["work_authorisation"],
                        row["notes"],
                        row["last_verified"],
                    ),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                    firm_id = cursor.lastrowid
                    connection.execute(
                        """
                        INSERT INTO applications (firm_id, stage, next_action_date)
                        VALUES (?, ?, ?)
                        """,
                        (firm_id, row["stage"], row["next_action_date"]),
                    )
                    applications_created += 1
                else:
                    skipped += 1

            distinct_keys = connection.execute(
                "SELECT COUNT(DISTINCT employer_key) FROM firms WHERE profile = ?",
                (profile,),
            ).fetchone()[0]
            for country, count in connection.execute(
                "SELECT country, COUNT(*) FROM firms WHERE profile = ? "
                "GROUP BY country ORDER BY country",
                (profile,),
            ):
                country_counts[country] = count
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    return {
        "rows_read": len(rows),
        "firms_inserted": inserted,
        "firms_skipped": skipped,
        "applications_created": applications_created,
        "distinct_employer_keys": distinct_keys,
        "country_counts": dict(country_counts),
    }


def print_summary(summary: dict[str, object]) -> None:
    """Print the import summary in a stable human-readable format."""
    print(f"Rows read: {summary['rows_read']}")
    print(f"Firms inserted: {summary['firms_inserted']}")
    print(f"Firms skipped: {summary['firms_skipped']}")
    print(f"Applications created: {summary['applications_created']}")
    print(f"Distinct employer keys: {summary['distinct_employer_keys']}")
    print("Firm counts per country:")
    for country, count in summary["country_counts"].items():
        print(f"  {country}: {count}")
