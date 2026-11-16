# Internship Tracker

A local Python dashboard that helps follow internship opportunities at selected firms: it tracks firms and applications, checks careers sources for matching openings, and reminds you of next actions.

**Status:** building the *triage* module, which decides, for each firm, how its careers page can be monitored (feed, structured data, recruiting system, plain HTML, JavaScript-only, or manual). The fetching layer, the analyzers, the orchestrator, the runner and the command-line tool are done and tested offline. The tool has never contacted a real website: the first live run is step T6. There is no dashboard yet.

| Step | Work | State |
|---|---|---|
| 0 | Source survey of 19 firms (`scripts/source_survey.py`, frozen baseline) | done |
| T1 | Triage contract: vocabulary, result types, verdicts, gate | done |
| T2 | HTTP layer: polite fetcher, response cache and replay, block detection | done |
| T3 | Analyzers: job links, structured data, feeds, recruiting systems, sitemaps, terms-of-use flag | done |
| T4 | Orchestration and command-line tool: firm-list reader, `triage_firm`, runner with resume, `python -m tracker.triage` | done |
| T5 | Fixtures and tests | next |
| T6 | First live run on the 19 firms, gate decision | planned |

## Project layout

```
src/tracker/triage/
  contract.py      vocabulary and result types (the code form of docs/triage_contract.md)
  decode.py        bytes to text: gzip/deflate, size cap, charset fallback
  robots.py        robots.txt matcher following RFC 9309
  transport.py     one HTTP GET with typed network errors
  cache.py         save every response to disk, replay later without network
  fetch_result.py  the typed outcome of one fetch (ok, blocked, failed)
  fetcher.py       polite fetcher: robots first, delays, retries, redirects, blocks
  urls.py          safe address resolution (never raises)
  html_scan.py     what the analyzers read from raw HTML
  listing.py       job links by the shape of an example job URL; app-shell marker
  vendors.py       recruiting-system hostnames
  structured.py    JSON-LD JobPosting, feed links, reading a feed
  sitemap.py       reading a sitemap
  terms.py         flag and quote sentences about automated access in terms of use
  firms_csv.py     read the firm list (semicolon CSV, UTF-8); a bad row never raises
  orchestrator.py  triage_firm(): fetch, run the analyzers, choose the mode, apply the budgets
  runner.py        run over all firms: one JSON line per firm, resume, gate summary
  cli.py           the command-line tool (python -m tracker.triage)
  __main__.py      lets `python -m tracker.triage` start the tool
tests/             offline tests (fake transport and clock, synthetic pages in several languages)
tests/local_fixtures/   real saved pages kept on the author's machine, not in the repository
docs/
  triage_contract.md   source of truth for the triage module
  t2_handoff.md        what T2 delivers and what is still open
  t3_handoff.md        what T3 delivers and what is still open
  t4_handoff.md        what T4 delivers and what is still open
scripts/source_survey.py   the first survey script, kept unchanged for comparison
```

## Running the tests

With the virtual environment active, from the project root (Python 3.12, standard library plus pytest only):

```powershell
python -m pytest -q
```

All tests run offline. A guard in `tests/conftest.py` makes any real network call fail. Checks on real saved pages live in `tests/test_triage_real_captures.py` and run only when `tests/local_fixtures/` exists; without it they are skipped (`python -m pytest -q -rs` shows why).

## Running the triage tool

From the project root, with the virtual environment active (PowerShell):

```powershell
$env:PYTHONPATH = "src"
python -m tracker.triage --input survey_input.csv --replay          # offline: answers only from the cache folder
python -m tracker.triage --input survey_input.csv --resume          # continue an interrupted run
python -m tracker.triage --input survey_input.csv --contact you@example.com   # LIVE run: contacts websites (T6)
```

Options: `--output` (default `triage_output.jsonl`, never overwritten), `--cache-dir` (default `triage_cache`), `--threshold` (default 8), `--contact` or the `SURVEY_CONTACT` variable (live runs only). Exit code 0 means the run finished (the gate result is printed, not an error), 2 means it could not start or write, 130 means Ctrl+C.

## How the tracker treats websites

- `robots.txt` is read first, including `Crawl-delay`, and obeyed.
- At most 1 request per second per host; 15 s timeout; retries only for timeouts, connection errors and server errors.
- A request that is refused (401, 403, 429) is never retried and the host is left alone.
- The User-Agent names the project and carries a contact address given at run time (never stored in the code).
- No logins, no CAPTCHAs, no LinkedIn or Indeed, and TLS certificate checking is never turned off.
- Terms of use are judged by a human: the tool only flags and quotes, and "not flagged" is never a clearance.
- A failed or blocked check is reported as such, never as "no openings". A firm is never called `manual` or `js-only` while a step that could have found a way to monitor it (example job page, feed, sitemap) failed, was refused or was skipped for budget: it becomes `failed` (unresolved) or `blocked` instead.
- Budgets per firm: 10 requests and 90 seconds, checked between steps, so one step can overshoot.

## Known limits

- The live network function has not yet been run against a real site; that happens in T6. Nothing in the project has run on macOS yet.
- Challenge-page (CAPTCHA) detection is not implemented until a real captured page is available.
- Feed reading and JSON-LD detection are built from the standards and tested on synthetic data; no real feed or job page has been captured yet.
- The terms-of-use patterns cover seven languages but have been checked on real pages in French and English only.
