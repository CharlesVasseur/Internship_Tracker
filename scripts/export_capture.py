"""Thin command-line wrapper for the offline triage capture helper."""

from pathlib import Path
import argparse
import sys


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tracker.captures import (
    export_entry,
    find_jsonld,
    find_locs,
    read_cache,
    select_entry,
    show_head,
)


def add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the common cache selection arguments to a subcommand."""
    parser.add_argument("--url-contains", required=True)
    parser.add_argument("--host")
    parser.add_argument("--index", type=int)


def main(argv: list[str] | None = None) -> int:
    """Parse command-line arguments and run one offline capture command."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--cache-dir", required=True)
    list_parser.add_argument("--host")
    list_parser.add_argument("--url-contains")

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--cache-dir", required=True)
    add_selection_arguments(show_parser)
    show_mode = show_parser.add_mutually_exclusive_group(required=True)
    show_mode.add_argument("--head", type=int, metavar="N")
    show_mode.add_argument("--locs", type=int, metavar="N")
    show_mode.add_argument("--jsonld", action="store_true")

    export_parser = subparsers.add_parser("export")
    export_parser.add_argument("--cache-dir", required=True)
    add_selection_arguments(export_parser)
    export_parser.add_argument("--out", required=True)
    export_parser.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "list":
        entries, warnings = read_cache(args.cache_dir)
        if not Path(args.cache_dir).is_dir():
            print(f"Error: cache folder not found: {args.cache_dir}", file=sys.stderr)
            return 2
        for warning in warnings:
            print(warning, file=sys.stderr)
        filtered = entries
        host_need = args.host.lower() if args.host is not None else None
        url_need = args.url_contains.lower() if args.url_contains is not None else None
        filtered = [
            entry for entry in filtered
            if (host_need is None or host_need in entry.host)
            and (url_need is None or url_need in entry.url.lower())
        ]
        for index, entry in enumerate(filtered):
            from tracker.captures import format_entry
            print(format_entry(index, entry))
        return 0

    entries, warnings = read_cache(args.cache_dir)
    if not Path(args.cache_dir).is_dir():
        print(f"Error: cache folder not found: {args.cache_dir}", file=sys.stderr)
        return 2
    for warning in warnings:
        print(warning, file=sys.stderr)
    entry, error, _ = select_entry(
        entries, args.url_contains, host=args.host, index=args.index,
    )
    if error is not None:
        print(error, file=sys.stderr)
        return 2
    assert entry is not None

    if args.command == "show":
        if args.head is not None:
            result = show_head(entry, args.head)
        elif args.locs is not None:
            result = find_locs(entry, args.locs)
        else:
            result = find_jsonld(entry)
        if not result.ok:
            print(result.text, file=sys.stderr)
            return 2
        print(result.text)
        return 0

    result = export_entry(entry, args.out, force=args.force)
    if not result.ok:
        print(result.text, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
