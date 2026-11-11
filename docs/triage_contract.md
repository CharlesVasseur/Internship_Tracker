# Triage contract (T1)

Source of truth for the triage module. If this file and a chat disagree, this file wins.
Code: `src/tracker/triage/contract.py`. Tests: `tests/test_triage_contract.py`.
Approved by Charles on 8 Nov 2026 (design change of 3 Nov 2026).
Amendment A1 (`sitemap_jobs`) was accepted on 8 Nov 2026.
Amendment A2 (`json-api`) is kept as RESERVED vocabulary: it is in the contract, but nothing
builds or checks it until the extraction step.
Amendment A3 (`http_4xx`) was accepted on 10 Nov 2026.

## Purpose

`triage(firm)` decides HOW a firm can be monitored. It runs unattended on firms we have
never seen. It classifies; it does not extract listings (a later step).

## Principles

1. One failing firm never crashes a run. Every firm gets exactly one result.
2. A failed or blocked check is never reported as "no openings" or "not monitorable".
3. "No" or "not found" is only reported when the page was reached and parsed correctly.
4. Every classification points to evidence: something observed, quoted or counted.
5. Item 10 (terms of use) stays a human judgment: the script flags and quotes only.
6. Never invent selectors, endpoints or page structures. Ask for page source or a capture.
7. Politeness: robots.txt first (and Crawl-delay), User-Agent naming the project and giving a
   contact address, at most 1 request per second per host, 15 s timeout with 2 retries,
   stop on 401/403/429, no logins, no CAPTCHAs, no LinkedIn/Indeed.

## Input: `FirmInput`

`name`, `careers_url`, and optional hints: `listing_url`, `example_job_url`, `terms_url`,
`api_url` (reserved: a JSON endpoint a human captured in the browser; GET only for now, a POST endpoint would need a method and body field added later). Inputs are never
validated: a bad URL becomes a `failed/invalid_url` result later, never an exception.

## Status: did the check work?

| status | meaning |
|---|---|
| ok | page reached, parsed, firm classified |
| empty | page reached and parsed but nothing job-related found. NOT "no openings". Needs a human look. |
| failed | a technical problem stopped the check (see error kinds) |
| blocked | the site or its robots.txt refused us (see block reasons) |

## Mode: how could the firm be monitored?

`feed`, `json-ld`, `json-api` (reserved), `vendor`, `html`, `js-only`, `manual`, `blocked`, `unknown`.
Priority when several apply: feed, json-ld, json-api, vendor, html, js-only, manual.

| mode | must be observed (required evidence kind) |
|---|---|
| feed | a feed fetched and parsed whose items link to job-like URLs (`feed_parsed`). A blog feed does not count. |
| json-ld | a JobPosting JSON-LD block (`jsonld_jobposting`) |
| json-api | reserved: a JSON endpoint given as a hint, fetched and parsed (`api_json`). Nothing produces it yet. |
| vendor | a hostname of a known recruiting system (`vendor_host`) |
| html | several job-like links in the raw HTML (`job_links`) |
| js-only | no job content in the raw HTML and app-shell markers (`spa_shell`) |
| manual | page parsed, no machine-readable path found (any evidence, e.g. `note` or `policy`) |
| blocked | status is blocked |
| unknown | status is failed or empty |

## Verdict: what do we do about it? (derived, never stored as truth)

| verdict | from |
|---|---|
| automatic | `feed`; or `json-ld` plus a source of job URLs (`job_links` or `sitemap_jobs` evidence) |
| probable | `json-ld` without a URL source; `vendor`; `html`; `json-api` |
| manual | `js-only`; `manual` |
| blocked | status blocked |
| unresolved | status failed or empty |

A `sitemap_line` (a Sitemap: line in robots.txt) is NOT a URL source. Only `sitemap_jobs`
(a sitemap that was fetched and lists job-like URLs) counts (accepted).

## Result invariants (enforced in code, tested)

- failed: `error` set, mode `unknown`, no block.
- blocked: `block` set, mode `blocked`, no error.
- empty: mode `unknown`, no error, no block.
- ok: mode in the ok modes, at least one evidence, the evidence kind its mode requires, no error or block.
- `checked_at` is an ISO timestamp; `evidence` and `requests` are tuples.
- failed, blocked and empty results may keep partial evidence (for example robots rules).

## Evidence kinds

`robots`, `redirect_chain`, `feed_link`, `feed_parsed`, `jsonld_jobposting`, `vendor_host`,
`job_links`, `sitemap_line`, `sitemap_jobs`, `spa_shell`, `api_json`, `policy`, `note`.
Each piece has `kind`, `url` (where it was seen) and `detail` (at most 300 characters,
quoted or counted).

## Error kinds (status failed) and whether to retry later

Retryable: `timeout`, `dns`, `connection`, `http_5xx`, `robots_unreachable`, `budget`.
Not retryable: `tls` (a certificate error does not fix itself; never turn verification off),
`http_4xx` (a normal page answered 4xx other than 401, 403 or 429, for example 404: the URL is
probably wrong or the page moved, so a human must look; a 404 on `robots.txt` is NOT an error, it
means there are no rules),
`decode`, `too_large`, `not_text`, `invalid_url`, `redirect_loop`, `internal` (a bug).

## Block reasons (status blocked)

`robots_disallow`, `http_401`, `http_403`, `http_429`, `challenge_page`, `captcha`, `login_wall`.
Repeated timeouts stay `failed` (we cannot prove a block); the error notes a possible silent block.

## Terms flag (item 10)

`flagged` (1 or 2 quotes, language guess), `not_flagged` (the script found nothing: NOT a
clearance), `not_assessed` (PDF, unsupported language, JavaScript page, fetch failed; says why).
The word "no" does not exist in this vocabulary. The flag never changes the verdict.

## Gate

PASS if at least 8 firms are `automatic`. FAIL only if automatic + probable + unresolved is
below 8. Otherwise UNDECIDED. Blocked and manual firms never count towards 8, and are reported
as blocked / manual. Threshold stays 8 for the 19 surveyed firms (40% of 19 rounds up to 8).

## Behaviour rules for later steps (not in this file's code)

| rule | step |
|---|---|
| Follow cross-host redirects (max 5) after checking robots.txt of each host | T2 |
| Honour `Crawl-delay`: wait the larger of 1 second and the delay for our agent. A delay above 10 s (an adjustable constant, default 10) is not waited for: the host fails with `failed/budget` and the message quotes the delay | T2 |
| robots.txt matching: our own matcher following RFC 9309 (wildcards `*` and `$`, longest match wins, Allow wins ties, unclear or unreadable rules mean not allowed), NOT `urllib.robotparser`, which reads wildcards literally (Milliman). Decided 10 Nov 2026 | T2 |
| robots.txt answers: 404 = no rules, everything allowed; 401/403/429 = `blocked`; 5xx or timeout = `failed/robots_unreachable`, nothing is fetched from that host | T2 |
| HTTP answers for normal pages: 2xx ok; 3xx followed; 401/403/429 = `blocked`, never retried, host stopped; other 4xx = `failed/http_4xx`; 5xx retried, then `failed/http_5xx` | T2 |
| robots.txt that cannot be used: tls and dns keep their own kind; timeout, connection error and 5xx (after 2 retries) become `robots_unreachable`; any other 4xx is `http_4xx`; a 200 body that is not robots rules (for example an HTML page) is `failed/decode` and allows nothing; the problem is remembered for the run so the host is not hammered. A robots disallow blocks only that URL, not the host. 3xx without Location, more than 5 redirects or a loop is `redirect_loop`; a redirect to a non-http address is `invalid_url`; a status outside 200-599 that is not otherwise handled is `internal` | T2 |
| TLS errors: one attempt, `failed/tls`, never retried, verification never disabled | T2 |
| Charset: header, BOM, `<meta>`, UTF-8, then cp1252 as a recorded fallback; an unknown name (for example `ISO-8852`) falls back, it does not fail | T2 |
| gzip and deflate bodies handled; size and JSON nesting capped | T2 |
| Block detection: 401/403/429 and robots disallow are blocked; challenge pages need a tiny body plus a known marker from a REAL captured page (no marker is written without a capture); the word "captcha" alone is not evidence | T2/T3 |
| Response cache and replay, so improving analysis never re-contacts a firm | T2 |
| Cache format: `triage_cache/<host>/<hash of url>.json` (folder set by `--cache-dir`, listed in `.gitignore`), one file per request holding the url, the time and either the wire body as base64 with status and headers, or the typed transport error. A live run with a cache writes every answer and failure; `--replay` reads only and has no network access; a url missing in replay is `failed/internal` "not in cache"; a broken cache file is `failed/internal`, never silent | T2 |
| Fetch the first sitemap (or the first file of a sitemap index) and stop reading at about 2 MB. Job-like means: the job-link shape rule below, applied to the `<loc>` URLs. Without an example job URL there is no `sitemap_jobs` evidence, only a note. Budget: 10 requests per firm. | T3 |
| If `api_url` is given, add a `note` evidence "api_url provided, not checked in this version", so a hint is never silently ignored | T4 |
| Job-link counting replaces the "title in raw HTML" check. SHAPE RULE (decided 11 Nov 2026 on three real captures: two listing pages and one sitemap; job words are NOT used, they matched 234 links on one real page against 32 real jobs): take the path segments of the firm's example job URL, N of them. The fixed part is the first N-1 segments, or all N if the id is in the query string (then candidates need the same query parameter names). Try prefixes from the longest fixed part down to the first two segments (or fewer if the fixed part is shorter). For each, count distinct links (relative ones resolved, fragments dropped) with the same host, the same prefix and exactly N segments. The first prefix with at least 5 wins and gives `job_links` evidence (listing page) or `sitemap_jobs` evidence (sitemap) naming the shape and the count. Otherwise no evidence, only a `note` saying why. A firm without an example job URL gets a `note`, never job-link evidence. Language prefixes (/en/ against /fr/) are not handled: no real capture shows them | T3 |
| App-shell marker: `spa_shell` evidence when the raw HTML has a `<div id="root">` with nothing inside it and no job links were found. Only the id `root` is recognised, because only it has been seen on a real capture; other ids are added when a real page shows them | T3 |
| Vendor table from hostnames observed in the survey: myworkdayjobs.com, oraclecloud.com, csod.com, softgarden.io, yello.co, ultipro.com, successfactors, icims, phenom; each entry says how it was verified | T3 |
| Terms patterns widened (EN, FR, ES, DE, NL, PT, IT); unsupported language is `not_assessed` | T3 |
| One guard per firm, a 90 s budget per firm, one JSONL line per firm written as it goes, `--resume` | T4 |

## Plan

| step | work |
|---|---|
| T1 | contract and tests (this) |
| T2 | HTTP layer, cache, block detection |
| T3 | analyzers |
| T4 | orchestration and CLI |
| T5 | fixtures and tests |
| T6 | run on the 19 surveyed firms, compare with the old survey, decide the gate |

The old `scripts/source_survey.py` stays unchanged as the T6 baseline.

## Needed from Charles before T3

Real files saved from the browser (no structure is invented): the Banque de France sitemap
(`.../recrutement-banque-de-france/siteMap.xml`, listed in its robots.txt) and the AXA sitemap
(`careers.axa.com/sitemap.xml` and the `sitemap1.xml` it points to, or the first part of it).
