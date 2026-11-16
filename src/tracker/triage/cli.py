"""The triage command-line tool (step T4.4).

    python -m tracker.triage --input survey_input.csv --contact you@example.com
    python -m tracker.triage --input survey_input.csv --replay          (no network, no waiting)
    python -m tracker.triage --input survey_input.csv --resume          (continue an interrupted run)

This file only connects the parts built before it: it reads the firm list,
chooses the transport (live and recording, or replay from the cache), runs
`triage_firm` on every firm through the runner, and prints the summary.

Exit codes:
    0    the run finished. The gate decision (PASS, UNDECIDED or FAIL) is printed
         but is not an error: it is a result for a person to read.
    2    the run could not start or could not write its results: bad arguments,
         unreadable firm list, no contact address, no cache folder in replay,
         an output file that already has results (without --resume), a write error.
    130  interrupted with Ctrl+C. Finished firms are saved; use --resume.

Messages for the person go to stdout (progress, summary); warnings and errors
go to stderr.

Live runs need a contact address, from --contact or the SURVEY_CONTACT
environment variable. It goes into the User-Agent header only: it is never
stored in the source code, the output file or the cache.

Pure Python standard library. The only network access is the `live_transport`
passed to main() (urllib_transport by default), and only in a live run.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO

from tracker.triage.cache import DEFAULT_CACHE_DIR, RecordingTransport, ReplayTransport, ResponseCache
from tracker.triage.fetcher import Fetcher
from tracker.triage.firms_csv import InputFileError, read_firms
from tracker.triage.orchestrator import triage_firm
from tracker.triage.runner import DEFAULT_THRESHOLD, OutputExists, format_summary, run_triage
from tracker.triage.transport import ContactRequired, Transport, build_user_agent, urllib_transport

EXIT_OK = 0
EXIT_CANNOT_RUN = 2
EXIT_INTERRUPTED = 130

DEFAULT_OUTPUT = "triage_output.jsonl"
REPLAY_CONTACT = "replay@example.invalid"      # never sent anywhere: replay has no network


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tracker.triage",
        description="Decide, for each firm in a CSV file, how its careers page can be monitored.",
    )
    parser.add_argument("--input", required=True, metavar="CSV",
                        help="the firm list: UTF-8, ';' separated (see docs/triage_contract.md)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, metavar="JSONL",
                        help=f"one JSON line per firm (default {DEFAULT_OUTPUT}); an existing file is never overwritten")
    parser.add_argument("--contact", metavar="EMAIL_OR_URL",
                        help="contact address put in the User-Agent; else the SURVEY_CONTACT variable. "
                             "Needed for live runs only")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, metavar="DIR",
                        help=f"where every answer is saved, and replayed from (default {DEFAULT_CACHE_DIR})")
    parser.add_argument("--replay", action="store_true",
                        help="answer only from the cache: no network, no waiting")
    parser.add_argument("--resume", action="store_true",
                        help="continue an earlier run: skip finished firms, retry retryable failures")
    parser.add_argument("--threshold", type=_positive_int, default=DEFAULT_THRESHOLD, metavar="N",
                        help=f"firms that must be automatic for the gate to pass (default {DEFAULT_THRESHOLD})")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    live_transport: Transport = urllib_transport,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Run the tool. Returns the exit code; never raises for expected problems.

    `live_transport`, `sleep` and `clock` exist so tests can run without network or waiting.
    A replay run ignores `sleep`: it never waits.
    """
    environ = os.environ if environ is None else environ
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    def say(message: str) -> None:
        print(message, file=out, flush=True)

    def complain(message: str) -> None:
        print(message, file=err, flush=True)

    try:
        args = build_parser().parse_args(argv)
    except SystemExit as stop:                     # argparse: 0 for --help, 2 for a usage error
        return stop.code if isinstance(stop.code, int) else EXIT_CANNOT_RUN

    try:
        firm_list = read_firms(args.input)
    except InputFileError as error:
        complain(f"error: {error}")
        return EXIT_CANNOT_RUN

    cache = ResponseCache(args.cache_dir)
    if args.replay:
        if not Path(args.cache_dir).is_dir():
            complain(f"error: --replay needs an existing cache folder, but {args.cache_dir!r} was not found")
            return EXIT_CANNOT_RUN
        transport: Transport = ReplayTransport(cache)
        agent = build_user_agent(args.contact or REPLAY_CONTACT, {})
        fetcher = Fetcher(transport, agent, sleep=lambda seconds: None, clock=clock)
        say(f"replay: answers come only from {args.cache_dir}; no network, no waiting")
    else:
        try:
            agent = build_user_agent(args.contact, environ)
        except ContactRequired as error:
            complain(f"error: {error}")
            return EXIT_CANNOT_RUN
        fetcher = Fetcher(RecordingTransport(live_transport, cache), agent, sleep=sleep, clock=clock)
        say(f"LIVE run: requests go to the firms' websites as {agent!r}; "
            f"every answer is saved in {args.cache_dir}")

    for warning in firm_list.warnings:
        complain(f"warning: {warning}")

    try:
        report = run_triage(
            firm_list.firms, lambda firm: triage_firm(firm, fetcher, clock=clock), args.output,
            resume=args.resume, report=say)
    except OutputExists as error:
        complain(f"error: {error}")
        return EXIT_CANNOT_RUN
    except KeyboardInterrupt:
        complain(f"interrupted. Finished firms are saved in {args.output}; "
                 "run the same command with --resume to continue.")
        return EXIT_INTERRUPTED
    except OSError as error:
        complain(f"error: cannot write the results: {error}")
        return EXIT_CANNOT_RUN

    for warning in report.warnings:
        complain(f"warning: {warning}")
    say("")
    say(format_summary(report.results, args.threshold, report.resumed))
    say(f"Results: {args.output}")
    return EXIT_OK
