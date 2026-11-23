"""Run one firm's automatic source check against a saved offline response cache."""

from pathlib import Path
import argparse
import sqlite3
import sys


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tracker.monitor import load_monitoring_config, replay_adapter
from tracker.queries import get_db_path


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, select exactly one firm, and print its replay result."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--db", default=get_db_path())
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--firm-id", type=int)
    selection.add_argument("--name-contains")
    parser.add_argument("--limit", type=int, default=10)

    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)

    if args.limit < 0:
        print("Error: --limit must be zero or greater", file=sys.stderr)
        return 2

    database_path = Path(args.db).expanduser()
    if not database_path.is_file():
        print(f"Error: database file not found: {database_path}", file=sys.stderr)
        return 2

    uri = database_path.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT id, name, source_config FROM firms "
                "WHERE monitoring = 'automated'"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        print(f"Error: cannot open database read-only: {error}", file=sys.stderr)
        return 2

    matches = []
    for row in rows:
        if args.firm_id is not None:
            if row["id"] == args.firm_id:
                matches.append(row)
        elif args.name_contains is not None:
            name = row["name"] or ""
            if args.name_contains.casefold() in name.casefold():
                matches.append(row)

    if len(matches) != 1:
        for row in matches:
            print(f"{row['id']} | {row['name']}", file=sys.stderr)
        print(
            f"Error: expected exactly one matching automated firm, found {len(matches)}",
            file=sys.stderr,
        )
        return 2

    row = matches[0]
    try:
        config = load_monitoring_config(row["source_config"])
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    result = replay_adapter(config, args.cache_dir)
    print(f"status: {result.status}")
    if result.error is not None:
        print(f"error: {result.error}")
    for item in result.evidence:
        print(item)
    print(f"listings: {len(result.listings)}")
    for listing in result.listings[: args.limit]:
        print(
            f"{listing.source_job_id or ''} | {listing.title} | "
            f"{listing.location_raw or ''} | {listing.description or ''}"
        )

    if result.status in ("ok", "empty"):
        return 0
    if result.status in ("failed", "blocked"):
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
