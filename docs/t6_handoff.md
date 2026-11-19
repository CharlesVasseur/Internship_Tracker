# T6 hand-off (snapshot at the end of T6, 19 Nov 2026)

Status: T6 (first live run, comparison with the old survey, gate decision) is done, plus two fixes found
by the live run. This note is a snapshot, not a second source of truth: the rules live in
`docs/triage_contract.md`. If the two disagree, the spec wins. Firm-by-firm results are kept on the
author's machine (not in the repository), because this repository holds no firm-specific text.

## What T6 delivered

| Step | Work | Result |
|---|---|---|
| T6.1 | one firm, live, response cache on | `ok / html / probable`, 5 requests. Gzip, charset header, empty `Disallow:` in robots.txt and the typed result all behaved as the offline tests assumed. |
| T6.2 | one firm that failed TLS in the old survey | `failed / tls` after exactly one attempt, host failure remembered, no further request. |
| T6.3 | the 19 firms, live, cache on | 83 requests in total (ceiling 190), at most 9 for one firm. Counts below. |
| T6.4 | old survey against live run | 7 firms were "likely automatic" in the old report; 1 is `automatic` live. |
| T6.5 | two fixes in `fetcher.py`, 8 new offline tests | default port is not part of the origin; a Crawl-delay holds for the whole host. |

## The live run in numbers (T6.3)

| Measure | Value |
|---|---|
| Verdicts | automatic 1, probable 11, manual 1, blocked 2, unresolved 4 |
| Terms of use (item 10) | flagged 3, not_assessed 5, not_flagged 11 |
| Gate (threshold 8) | UNDECIDED: 1 automatic of 19; best case if every probable and unresolved firm works out: 16 |
| Hosts that refused us | 3 (two HTTP 429, one HTTP 403); one of the 429 was only on a terms host, so the firm itself stayed `ok` |
| Hosts that failed TLS | 5 (four primary-page hosts, one terms host); cause unknown |

The gate is UNDECIDED by the contract's own rule. Thresholds and verdict definitions were not changed to
reach a PASS.

## What the live run taught us

- The transport works on real sites: gzip, charset from the header, 301 redirects, typed 404, 410, 429 and
  403, a typed TLS error with one attempt, and every answer and failure saved in the cache.
- The RFC 9309 matcher was right and the old survey was wrong on a wildcard rule (`Allow: */JobBoard/`).
  The old script read wildcards literally.
- Sites changed since the old survey (12 Oct): one site now answers 429 on its first request (a CDN in front
  of a recruiting system), and two recruiting-system hosts now fail TLS.
- Example job URLs expire: 3 of 19 returned 404 or 410, so JSON-LD could not be tested for them. This is
  a data upkeep problem in the firm list, and the tool reported it correctly as a note.
- The first real feed was a blog feed (194 items): it was parsed and correctly rejected as not a job feed.
  A job feed was found once but not fetched: the site's own robots.txt disallows it, and the tool obeyed.
- Only the first `Sitemap:` line is read. In one case the first of 7 lines is a regional sitemap with no
  global jobs, so a firm with JSON-LD stayed `probable`.
- A threshold of 5 job-shaped links keeps a very small careers page at `manual`.
- The terms analyzer flagged 3 firms with quotes and caught a clause the old script had missed.

## Rules decided in T6 (see the spec)

- Crawl-delay belongs to the host (name without scheme or port): once any origin of a host has asked for
  it, every later request to that host waits, including robots.txt of another origin. A delay over the cap
  is not remembered for other origins. Decided 18 Nov 2026.
- The origin that keys robots.txt rules leaves out the default port (443, 80). `http` and `https` stay two
  origins. Decided 18 Nov 2026.

## Open items

| Item | When |
|---|---|
| Decide what follows an UNDECIDED gate (for example extraction on the probable firms, or a redesign) | the author |
| Diagnose the TLS failures on 5 hosts without ever switching verification off; first clue: the two messages differ ("self-signed certificate in certificate chain", "unable to get local issuer certificate"); a cheap first test is the certificate path shown by a browser | later |
| Decide whether to try the refused hosts again some other day (a human decision; refusals are never retried by the tool) | the author |
| Refresh the stale example job URLs in the firm list before the next live run | before next live run |
| Read the terms of use by hand: 3 flagged firms, 5 not assessed | the author |
| Idea, needs a SPEC CHANGE first: choose the sitemap that matches the example job's locale when there are several `Sitemap:` lines | later |
| Idea, needs a SPEC CHANGE first: a lower job-link threshold for very small careers pages | later |
| Real captures to export from the cache into `tests/local_fixtures/` (git-ignored; strip `set-cookie` headers first): a job page with JSON-LD (two firms), the blog feed, the empty sitemap, the http-to-https 301 pair, the two refusal pages | when exported |
| Still missing as real captures: a job feed, a sitemap index, a challenge page (CAPTCHA), a terms page in Spanish, German, Dutch, Portuguese or Italian | when available |
| Replay the real cache offline after the T6.5 fix and compare with the live results (one firm should need one request fewer) | next offline check |
| Request start times are not logged (the cache keeps the response time), so live pacing is only consistent with the rules, not measured | optional, small change |

## What is verified, and what is not

- Verified on the real network: everything in "What the live run taught us", on 19 firms and 35 hosts.
- Verified offline: the two T6.5 fixes (8 tests, red before the fix, green after), and the whole suite
  (1379 tests on the author's machine).
- NOT verified: the cause of any TLS failure or refusal; pacing measured at request start; replay of the real
  cache after T6.5; anything on a real challenge page, feed of jobs, sitemap index or terms page in five languages.

## Known limits

- Budgets are checked between steps, so one step can overshoot (a new host costs robots.txt plus the page).
- A Crawl-delay over the cap (10 s) is not shared with other origins of the host.
- The live result is one run on one day from one machine; sites change.
- The cache keeps full response headers (cookies included): it is git-ignored and must never be copied
  as it is.
