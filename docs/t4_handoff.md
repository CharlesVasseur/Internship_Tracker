# T4 hand-off (snapshot at the end of T4, 16 Nov 2026)

Status: T4 (orchestration and command-line tool) is done, all tests offline. This note is a
snapshot, not a second source of truth: the rules live in `docs/triage_contract.md`. If the two
disagree, the spec wins.

## What T4 delivers

| File | Job | Names later steps use |
|---|---|---|
| `firms_csv.py` | read the firm list (`;`, UTF-8); a bad row never raises | `read_firms`, `parse_firms`, `FirmList`, `InputFileError` |
| `orchestrator.py` | triage one firm: fetch, analyze, choose the mode, budgets, guard | `triage_firm`, `choose_mode` |
| `runner.py` | loop over firms: JSONL line per firm, resume, summary | `run_triage`, `format_summary`, `read_results`, `RunReport`, `OutputExists` |
| `cli.py`, `__main__.py` | `python -m tracker.triage` | `main`, `build_parser` |

## How it fits together

```
read_firms(csv) -> run_triage(firms, lambda firm: triage_firm(firm, fetcher), output, resume=...)
                -> format_summary(results, threshold)         # gate decision from the contract
```
`triage_firm` order: LinkedIn/Indeed policy check, primary page (listing URL, else careers URL; its
fetch sets the status), listing analyzers, example job page (JSON-LD), first feed, first sitemap
(only if no job links and no job feed yet), terms page. Mode priority: feed, json-ld, json-api,
vendor, html, js-only, manual. Budgets: 10 requests, 90 s per firm, checked between steps.

## Rules decided in T4 (see the spec)

- T4 never produces `empty`. A page that loads with no text and no links is `manual` with a note.
- A firm that would be `manual` or `js-only` while the example job page, feed or sitemap failed,
  was refused or was skipped for budget is `failed` (unresolved) or `blocked`, never `manual`.
- The terms page is read even if the primary page failed; the terms flag never changes a verdict.
- The runner never overwrites an output file; resume re-runs only retryable failures.

## Open items

| Item | When |
|---|---|
| First live run: `urllib_transport` has never run against a real site | T6 |
| Fixtures and extra tests, firm-neutral; run the suite on macOS as well (see the README) | T5 |
| Fix the firm list | before T6 |
| `TriageResult.from_json` raises AttributeError (not ContractError) on a JSON list or number; the runner tolerates it | later, with a test |
| A real Spanish terms page (MAPFRE "Advertencias"); challenge-page markers need a real capture | when available |
| Spec rows B1 to B3 in `docs/spec_rows_T4.md` wait for approval | start of T5 |

## Known limits

- Budgets are checked between steps: one step can overshoot (a new host costs robots.txt plus the
  page, each with up to 3 attempts; a slow site about 51 s).
- Replay uses the real clock with no waiting; a run that hit the time budget live will not hit it in replay.
- The tool needs `PYTHONPATH=src` (or an editable install) to run with `python -m`; there is no packaging file yet.
- `os.fsync` after each result line and `\n` line endings are tested on Linux in the sandbox and on
  Windows by the author; macOS is untested.
