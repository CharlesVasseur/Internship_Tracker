# Test matrix (end of T5, 17 Nov 2026)

Which test checks which rule of `docs/triage_contract.md`, and what is still unchecked.
It replaces the T5.1 snapshot taken before the gap tests. This file is a snapshot, not a second source of
truth: the spec wins.

## Numbers

| | |
|---|---|
| Tests on the author's machine | 1371 pass (all of them, including the real-capture tests and the firm-name check) |
| Tests in continuous integration | 1365 pass and 6 are skipped (the five real-capture tests and the firm-name check: their files are not in the repository) |
| Coverage of `src/tracker/triage` | 98% of lines and branches (1953 statements, 31 missed, 14 partial branches); measured in an analysis run, not part of the project |

Status words: **Covered** = at least one test fails if the rule is broken. **Partly** = some of the rule is
checked. **Not buildable yet** = the spec itself says it waits for a real capture.

## Principles, input, status, mode, verdict, invariants

| Spec item | Tests (file: name fragments) | Status |
|---|---|---|
| P1 one failing firm never crashes a run | runner: `a_firm_that_raises_becomes_failed_internal...`; orch: `an_unexpected_exception_becomes_failed_internal...`, `a_contract_error_is_caught_by_the_guard`; gaps: `random_pages_and_answers_never_crash_triage...` | Covered |
| P2 a failed or blocked check is never "no openings" / "not monitorable" | orch: the secondary-step tests; scenarios: `a_failed_or_blocked_result_never_claims_to_be_manual_or_empty` (35 scenarios) | Covered |
| P3 "not found" only when the page was parsed | orch: `a_job_page_without_json_ld_says_what_was_read`, `honest_not_founds_stay_manual_or_js_only` | Covered |
| P4 every classification has evidence | contract: `ok_needs_an_ok_mode_and_evidence`, `each_mode_demands_its_own_kind_of_evidence` | Covered |
| P5 terms of use stay a human judgment | contract: `a_terms_flag_does_not_change_the_verdict`; orch: `a_terms_ban_is_flagged_and_never_changes_the_verdict`; scenarios: the eight terms scenarios | Covered |
| P6 never invent selectors or endpoints | a working rule, not testable | n/a |
| P7 politeness | scenarios: `every_request_in_every_scenario_is_polite` (robots.txt first on each host, User-Agent with contact, 15 s timeout, 1 s between requests to a host, never LinkedIn or Indeed); fetcher: retry, 401/403/429 and TLS tests. "No logins, no CAPTCHAs" is an absence: no code path exists | Covered |
| FirmInput never validated | contract; csv: bad-row tests; gaps: unusable-address tests | Covered |
| Status, mode and verdict tables | contract tests; scenarios cover every verdict and every mode that can be produced (`json-api` is reserved) | Covered |
| Result invariants | contract: failed / blocked / empty / ok invariants, partial evidence, tuples, JSON round trip | Covered |
| Evidence kinds (13) | contract vocabulary tests; every kind except the reserved `api_json` is produced end to end in the scenarios | Covered |
| Error kinds (14) | contract: `every_error_kind_can_be_built`; each kind is produced by a fetcher, decode or orchestrator test; six of them also by scenarios | Covered |
| Block reasons (7) | `robots_disallow`, `http_401/403/429` produced and tested. `challenge_page`, `captcha`, `login_wall` exist only as vocabulary | Partly, not buildable yet |
| Terms flag vocabulary | contract and terms tests | Covered |
| Gate | contract; runner: summary tests; scenarios: summary counts match the expected verdicts | Covered |

## The 31 behaviour rows (spec order, with the T5 row last)

| # | Rule | Tests | Status |
|---|---|---|---|
| 1 | Redirects, max 5, robots per host | fetcher: redirect tests; orch: `a_redirect_is_recorded_as_evidence`; scenarios: `redirect_on_the_listing` | Covered |
| 2 | Crawl-delay, cap 10 s | robots: delay parsing; fetcher: delay and cap tests; scenarios: `failed_crawl_delay_too_long` | Covered |
| 3 | Own RFC 9309 matcher, not `robotparser` | robots: 37 tests, including three situations rebuilt with synthetic text; scenarios: `blocked_robots_wildcard` | Covered |
| 4 | robots.txt answers | fetcher: no-file, refused-file, server-error tests | Covered |
| 5 | HTTP answers for pages | fetcher tests; scenarios: `blocked_http_403`, `blocked_http_429`, `failed_page_not_found`, `failed_server_error` | Covered |
| 6 | Unusable robots.txt, loops, odd statuses | fetcher: the robots-failure tests, loop and status tests | Covered |
| 7 | TLS: one attempt, never disabled | fetcher; transport; scenarios: `failed_certificate_error` | Covered |
| 8 | Charset order and fallback | decode and fetcher tests | Covered |
| 9 | gzip/deflate, size cap, JSON nesting cap | decode tests; structured: deeply nested block | Covered |
| 10 | Block detection | status codes and robots disallow covered. Challenge pages: no marker is written without a real capture | Partly, not buildable yet |
| 11 | Cache and replay | cache tests; fetcher replay tests; scenarios: live run then replay for every scenario | Covered |
| 12 | Cache format and `.gitignore` | cache and fetcher tests; hygiene: `gitignore_has_the_lines_the_project_relies_on...` | Covered |
| 13 | First sitemap / first child, 2 MB | sitemap: 18 tests; scenarios: `sitemap_index_first_child`, `sitemap_cut_at_the_size_cap` | Covered |
| 14 | `api_url` note | orch: `an_api_url_is_noted_and_never_requested` | Covered |
| 15 | Job-link shape rule | listing: 30 tests; real captures (local only): 3 | Covered |
| 16 | App-shell marker, only `root` | listing tests; scenarios: `mode_js_only_manual` | Covered |
| 17 | Vendor table by host label | vendors: 14 tests; scenarios: `mode_vendor_probable` | Covered |
| 18 | Feeds: lead, parsed, DOCTYPE refusal | structured and orch feed tests; scenarios: `mode_feed_automatic`. Synthetic feeds only | Covered |
| 19 | Terms patterns, 7 languages | terms tests; scenarios: English and French. Synthetic for five languages | Covered |
| 20 | Terms result and `not_assessed` reasons | terms tests; scenarios: PDF, app shell, page not found | Covered |
| 21 | Truncation | decode, fetcher and cache tests; scenarios: `sitemap_cut_at_the_size_cap` | Covered |
| 22 | Terms list rule | terms: the list-lead-in tests | Covered |
| 23 | Never fetched (LinkedIn/Indeed) | orch: 4 tests; gaps: malformed-address test; scenarios: `policy_linkedin` | Covered |
| 24 | Budgets and guard | orch and gaps: default cap of 10 reached, request and time budgets, primary page always fetched, guard | Covered |
| 25 | Sitemap skipped when URLs known; terms read after failure | orch tests; scenarios: `terms_read_after_a_failed_page`, `terms_not_asked_from_a_host_that_blocked_us` | Covered |
| 26 | No `empty` status | orch tests; scenarios: `mode_manual_blank_page`; gaps: random-input test | Covered |
| 27 | Secondary steps | orch: the secondary-step tests; gaps: feed or sitemap skipped for budget; unusable feed or Sitemap address; scenarios: three secondary-step scenarios | Covered |
| 28 | Firm list | csv: 42 cases | Covered |
| 29 | Output and resume | runner: 34 cases; cli; scenarios: resume over all scenarios | Covered |
| 30 | Command-line tool | cli: 29 cases; gaps: runner warnings shown; scenarios: live then replay | Covered |
| 31 | Unusable addresses (T5) | gaps: address cases for `triage_firm` and a direct fetch, tab not deleted into another host, space in a path still usable, redirect to an unusable host | Covered |

Result: 30 rows Covered, 1 Partly (row 10, waiting for a real challenge-page capture).

## Gaps from the T5.1 snapshot

| # | Gap | State |
|---|---|---|
| G1 to G9 | default cap reached, feed or sitemap skipped for budget, unusable feed or Sitemap address, malformed URL in the LinkedIn check, two defensive branches, CLI warnings, contract validation | closed in T5.4 |
| G10 | `from_json` on `[]`, `5` or `"text"` raised `AttributeError` | fixed and tested |
| G11 | random input on `triage_firm` | closed: 250 seeded rounds |
| G12 | no end-to-end scenario test | closed in T5.3 |
| G13 | repository hygiene (`.gitignore` lines, no firm names) | closed; widened to `docs/` and the README in T5.6 |
| G14 | `urllib_transport` and its redirect handler never run | open: T6 (needs the real network) |
| G15 | small defensive lines in cache, decode, fetcher, html_scan, listing, vendors, structured, csv, runner | open, left on purpose; `__main__.py` runs only inside a subprocess test, so coverage cannot see it |

## Notes

- Firm names were once present in the repository (tests, docstrings, three real robots.txt files). They were
  replaced by synthetic text in T5.2b and are now kept out by the hygiene test.
- The sandbox used for the analysis sees 8 tests fewer than the author's machine; the author's count is the reference.
