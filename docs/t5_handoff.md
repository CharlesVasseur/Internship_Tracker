# T5 hand-off (snapshot at the end of T5, 17 Nov 2026)

Status: T5 (fixtures and tests) is done. This note is a snapshot, not a second source of truth: the rules
live in `docs/triage_contract.md`. If the two disagree, the spec wins. Which test checks which rule is in
`docs/test_matrix.md`.

## What T5 delivers

| Item | Job |
|---|---|
| `tests/fixtures/scenarios/` (35 folders) | one synthetic scenario each: its pages, the scripted answers and the expected result, written from the spec before they were run |
| `tests/scenario_loader.py` | test support: loads the scenarios, builds a fake transport from them, and `mismatches(result, expected)` |
| `tests/test_triage_scenario_fixtures.py` | the fixtures are well-formed, firm-neutral, valid against the vocabulary, and cover every mode that can be produced |
| `tests/test_triage_scenarios.py` | each scenario through `triage_firm`, all of them through `run_triage` (JSONL, resume, summary), each through the CLI live and then replayed; politeness checked in every scenario |
| `tests/test_triage_gaps.py` | budgets, unusable addresses, defensive branches, the CLI's warnings, contract validation, JSON that is not a result, 250 seeded random pages through `triage_firm` |
| `tests/test_repo_hygiene.py` | the `.gitignore` lines the project relies on; no firm name from the local firm list in `src/`, `tests/`, `docs/` or the README |
| `.github/workflows/tests.yml` | the suite on Windows, macOS and Linux (pinned images, Python versions, pytest and actions) |
| `tests/test_triage_robots.py` | the three situations once taken from real robots.txt files are rebuilt with synthetic text |

Code changes found by the tests:
- `TriageResult.from_dict` now raises `ContractError` (not `AttributeError`) on JSON that is not a result.
- A host containing a space or another control character is `failed/invalid_url` with no request (spec row "Unusable addresses").

## Rules decided in T5

- Fixtures are firm-neutral; real pages stay in `tests/local_fixtures/` (ignored by git).
- A scenario's expectation is written from the spec, then checked against the code; a mismatch is investigated, never copied.
- The firm-name check reads the local firm list, so no firm name is written into the repository to look for it.
- CI pins runner images, Python versions, pytest and the two official actions (by commit hash); it runs only the offline tests.

## Open items

| Item | When |
|---|---|
| First live run: `urllib_transport` has never run against a real site | T6 |
| Fix the firm list: one name lost its accent, two firms use the listing URL as the example job URL, one has no example job URL | before T6 |
| A real terms page in Spanish, German, Dutch, Portuguese and Italian; a real feed; a job page with JSON-LD; a sitemap index; a challenge page (needed for block markers) | when captured |
| About 30 uncovered lines remain, mostly defensive branches (cache, decode, fetcher, html_scan, listing, vendors); `__main__.py` runs only inside a subprocess test | leave unless cheap |
| Update the actions pinned by commit hash by hand when new releases appear (Dependabot could do it) | later |

## What is verified, and what is not

- Verified offline: every spec rule listed in `docs/test_matrix.md`, on the fake transport.
- Verified by CI once the first run is green: Windows, macOS and Linux, Python 3.12.8 and 3.12.3.
- NOT verified: anything on the real network, real pages beyond the local captures, terms pages in five languages.

## Known limits

- Budgets are checked between steps, so one step can overshoot (a new host costs robots.txt plus the page).
- The random-input test uses a fixed seed: it is deterministic, not exhaustive.
- Coverage numbers come from the author's analysis runs, not from a tool in the project (no extra dependency).
