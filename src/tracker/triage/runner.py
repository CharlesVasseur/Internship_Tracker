"""Run triage over a list of firms (step T4.3).

    run_triage(firms, triage, "triage_output.jsonl", resume=False)

`triage` is any function that takes a FirmInput and returns a TriageResult. In
the real tool it is `triage_firm` with a Fetcher bound to it; in tests it is a
small stub, so this file never needs a network.

What the runner guarantees:

* One result per firm, written as ONE LINE of JSON the moment the firm is
  done, then flushed to disk. A crash or Ctrl+C loses at most the firm that
  was in progress.
* A firm that makes `triage` raise, or return something that is not a
  TriageResult, becomes a failed/internal line. The run goes on.
  (`triage_firm` already has its own guard; this one protects the loop itself,
  because the function is injected.) Writing the output file is NOT guarded:
  a full disk or a locked file stops the run loudly instead of losing results.
* An existing, non-empty output file is never overwritten. Without `resume`
  the run refuses to start (OutputExists). With `resume` it reads the file
  first and skips the firms that are already done.
* Resume re-runs a firm only if its stored result is a failed one that the
  contract calls retryable (timeout, dns, connection, http_5xx,
  robots_unreachable, budget). Blocked results and non-retryable failures
  (tls, http_4xx, ...) are kept: asking again would not help, and blocked
  hosts must be left alone. A line that cannot be read is ignored and
  reported. If a firm has several lines, the last one wins.
* A firm is identified by (name, careers_url). A repeated row in the input is
  run once and reported.

Also here: `format_summary`, the text that ends a run (counts per verdict,
terms flags, and the gate decision from the contract).

Pure Python standard library. No network access in this file.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from tracker.triage.contract import FirmInput, TriageError, TriageResult, gate_decision

DEFAULT_THRESHOLD = 8

Triage = Callable[[FirmInput], TriageResult]
Key = tuple[str, str]


class OutputExists(Exception):
    """The output file already has content and `resume` was not asked for."""


@dataclass(frozen=True)
class RunReport:
    results: tuple[TriageResult, ...]     # one per distinct firm, in input order (resumed ones included)
    resumed: int                          # how many of them came from the output file
    warnings: tuple[str, ...]


def firm_key(firm: FirmInput) -> Key:
    return (firm.name, firm.careers_url)


# ---------------------------------------------------------------------------
# Reading an earlier output file
# ---------------------------------------------------------------------------


def read_results(path: Path) -> tuple[dict[Key, TriageResult], list[str]]:
    """The stored results by firm (the last line of a firm wins), and warnings for unreadable lines."""
    results: dict[Key, TriageResult] = {}
    warnings: list[str] = []
    if not path.exists():
        return results, warnings
    # errors="replace": a damaged byte makes that line unreadable, not the whole file.
    text = path.read_text(encoding="utf-8", errors="replace")
    # split on "\n" only: str.splitlines() would also cut at characters such as U+2028
    # that can legitimately appear inside a JSON string.
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            result = TriageResult.from_json(line)
        except Exception:        # any failure to read one line (not only ContractError) just skips it
            warnings.append(f"{path.name} line {number}: not a readable result, ignored")
            continue
        results[firm_key(result.firm)] = result
    return results, warnings


def needs_rerun(result: TriageResult) -> bool:
    """Only a failed result with a retryable error is worth asking again."""
    return result.status == "failed" and result.error is not None and result.error.retryable


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run_triage(
    firms: Iterable[FirmInput],
    triage: Triage,
    output_path: str | Path,
    *,
    resume: bool = False,
    report: Callable[[str], None] = lambda message: None,
) -> RunReport:
    path = Path(output_path)
    previous: dict[Key, TriageResult] = {}
    warnings: list[str] = []
    has_content = path.exists() and path.stat().st_size > 0
    if has_content:
        if not resume:
            raise OutputExists(
                f"{path} already has results. Use --resume to continue that run, "
                "or choose another output file. Nothing was changed.")
        previous, warnings = read_results(path)

    queue: list[FirmInput] = []
    seen: set[Key] = set()
    for firm in firms:
        key = firm_key(firm)
        if key in seen:
            warnings.append(f"{firm.name!r} appears more than once with the same careers URL: run once")
            continue
        seen.add(key)
        queue.append(firm)

    path.parent.mkdir(parents=True, exist_ok=True)
    final: dict[Key, TriageResult] = {}
    resumed = 0
    with path.open("a", encoding="utf-8", newline="\n") as out:
        if has_content and not _ends_with_newline(path):
            out.write("\n")                 # a half-written last line stays alone on its line
        for position, firm in enumerate(queue, 1):
            label = f"[{position}/{len(queue)}] {firm.name}"
            key = firm_key(firm)
            old = previous.get(key)
            if old is not None and not needs_rerun(old):
                final[key] = old
                resumed += 1
                report(f"{label}: already done ({_describe(old)}), skipped")
                continue
            if old is not None:
                report(f"{label}: retrying ({_describe(old)})")
            result = _guarded(triage, firm)
            out.write(result.to_json() + "\n")
            out.flush()
            os.fsync(out.fileno())
            final[key] = result
            report(f"{label}: {_describe(result)}")
    return RunReport(tuple(final.values()), resumed, tuple(warnings))


def _ends_with_newline(path: Path) -> bool:
    with path.open("rb") as handle:
        handle.seek(-1, os.SEEK_END)
        return handle.read(1) == b"\n"


def _guarded(triage: Triage, firm: FirmInput) -> TriageResult:
    try:
        result = triage(firm)
        if not isinstance(result, TriageResult):
            raise TypeError(f"triage returned {type(result).__name__}, not a TriageResult")
        return result
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        return TriageResult.failed(firm, TriageError.of("internal", message, firm.careers_url or None))


def _describe(result: TriageResult) -> str:
    text = f"{result.status} {result.mode} {result.verdict}"
    if result.error is not None:
        text += f" [{result.error.kind}]"
    if result.block is not None:
        text += f" [{result.block.reason}]"
    return f"{text}, {len(result.requests)} request(s)"


# ---------------------------------------------------------------------------
# The summary at the end of a run
# ---------------------------------------------------------------------------


def format_summary(
    results: Iterable[TriageResult], threshold: int = DEFAULT_THRESHOLD, resumed: int = 0
) -> str:
    items = list(results)
    gate = gate_decision(items, threshold)
    origin = f" ({resumed} taken from the output file)" if resumed else ""
    counts = gate.counts
    terms = Counter(item.terms.status if item.terms else "no terms result" for item in items)
    lines = [
        f"Triage summary: {len(items)} firm(s){origin}",
        "  automatic {automatic}, probable {probable}, manual {manual}, blocked {blocked}, "
        "unresolved {unresolved}".format(**counts),
        "  terms of use (item 10 stays a human judgment): "
        + ", ".join(f"{name} {terms[name]}" for name in sorted(terms)),
        f"Gate (threshold {gate.threshold}): {gate.decision}",
        f"  {gate.explanation}",
    ]
    for verdict in ("blocked", "unresolved"):
        names = [f"{item.firm.name} ({_reason(item)})" for item in items if item.verdict == verdict]
        if names:
            lines.append(f"{verdict.capitalize()}: " + "; ".join(names))
    return "\n".join(lines)


def _reason(result: TriageResult) -> str:
    if result.error is not None:
        return result.error.kind
    if result.block is not None:
        return result.block.reason
    return result.status
