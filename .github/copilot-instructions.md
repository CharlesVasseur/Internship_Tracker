# Instructions for GitHub Copilot in this repository

This is a local Python dashboard that helps a student follow internship opportunities at firms they choose.
Streamlit for the UI, SQLite for storage, Python 3.12, Windows with PowerShell. Most of the code is written
with AI; the owner (Charles) directs, runs and checks it, and must be able to explain every file in an
interview. Write code he can read and defend.

## Read first
- `README.md` (status), `docs/triage_contract.md` (source of truth for the triage module),
  `docs/t6_handoff.md` (where the project stands). If a task conflicts with these, stop and ask.

## Never do
- **Never contact any website.** No HTTP requests to firms' sites, no scraping, no browser automation, no
  running `python -m tracker.triage` unless it has `--replay`. All work is offline. If a task needs the real
  structure of a page, stop and ask Charles to paste the page source or a network capture.
- **Never invent** selectors, endpoints, API fields or page structures. Say what you verified and what you assumed.
- **Never hide a failure.** No bare `except:` or `except Exception: pass`. A failed or blocked check is never
  reported as "no openings" and never changes the state of an opportunity; show the error and when the source
  last worked.
- Do not add a dependency. Standard library plus pytest only, with one planned exception: Streamlit for the UI
  (check its current documentation before using any API). Anything else: ask first.
- Do not modify `src/tracker/triage/`, its tests or `docs/triage_contract.md` unless the task says so. The
  existing tests must keep passing (`python -m pytest -q`).
- Do not commit or push. Charles commits. Do not edit `DECISIONS.md` or `AI_MISTAKES.md`: propose lines in your reply.
- Never put a contact address, password, key or personal data in code, tests, docs or fixtures.

## Repository rules
- No firm-specific names or text in `src/`, `tests/`, `docs/`, `.github/` or the README. Fixtures and demo data are
  synthetic (`Example Firm`, hosts on `example.test`). Real data lives only in git-ignored places
  (`data/`, `*.local.yaml`, `survey_*`, `triage_cache/`, `triage_output*.jsonl`, `tests/local_fixtures/`).
- New code goes under `src/tracker/` next to the existing `triage` package (planned: `models.py`, `db.py`,
  `adapters/`, `matching/`, `pipeline/`, `gamification/`, `analytics/`, `ui/`). Tests under `tests/`.
- Politeness rules for anything that will one day fetch pages: read robots.txt first, 1 request per second per
  host, identify the project in the User-Agent, stop on 401, 403 or 429, no logins, no CAPTCHAs, never
  LinkedIn or Indeed. (Do not implement fetching unless the task asks for it.)

## Style
- Simple and readable over clever. Small functions, clear names, a one-line docstring saying what and why.
- Typed result values for outcomes that can fail (ok, empty, failed, blocked) instead of exceptions or None.
- Commands are for PowerShell. Set `$env:PYTHONPATH = "src"` before `python -m ...`.

## Tests (light safety net)
- No tests for Streamlit pages: end the task with a short manual checklist instead.
- Do write small offline tests for logic where a wrong result looks plausible in the UI: points (the same event
  never scores twice, priority weights), state changes of opportunities (a failed or blocked check never closes
  or changes one; missing counts), the matcher, and one smoke test that the database initialises and the import
  runs on demo data.
- Tests are offline. A guard in `tests/conftest.py` makes any real network call fail; keep it that way.

## How to finish every task
Reply with: (1) files created or changed, (2) how to run and check it (commands and what Charles should see),
(3) your assumptions, listed one by one so he can validate them, (4) open questions, (5) `git status --short`.
Work on one task at a time, stop when it is done, and say clearly if the task grew beyond what was asked.
