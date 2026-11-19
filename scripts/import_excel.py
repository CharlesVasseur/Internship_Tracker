"""Command-line wrapper for the offline Excel importer."""

from pathlib import Path
import argparse
import sys


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tracker.importer import ImportValidationError, import_excel, print_summary


def main(argv: list[str] | None = None) -> int:
    """Run the Excel import command."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--xlsx", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    try:
        summary = import_excel(args.xlsx, args.db, args.profile)
    except ImportValidationError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
