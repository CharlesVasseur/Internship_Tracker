# T3 hand-off (snapshot at the end of T3, 12 Nov 2026)

Status: T3 (analyzers) is done. The analyzers are pure: text in, evidence out. They never fetch and
never choose the final mode; that is T4. This note is a snapshot, not a second source of truth: the
rules live in `docs/triage_contract.md`. If the two disagree, the spec wins.

## What T3 delivers

| File | Job | Names later steps use |
|---|---|---|
| `html_scan.py` | read anchors, resource addresses, JSON-LD blocks, feed links and the empty-root marker from raw HTML | `scan_html`, `PageScan` |
| `urls.py` | resolve an address found in a page without ever raising | `resolve_url` |
| `listing.py` | job links by the shape of the example job URL; app-shell marker | `analyze_listing`, `count_job_links`, `MIN_JOB_LINKS` |
| `vendors.py` | recruiting-system hostnames (table from the survey, matched by host label) | `analyze_vendors`, `find_vendors`, `VENDORS` |
| `structured.py` | JSON-LD `JobPosting`, feed links, reading a fetched feed | `analyze_jobposting`, `find_feed_links`, `analyze_feed` |
| `sitemap.py` | read a fetched sitemap by text search (urlset or index) | `read_sitemap`, `SitemapFindings` |
| `terms.py` | flag and quote sentences about automated access, 7 languages | `analyze_terms`, `terms_from_fetch` |

## How T4 plugs the pieces together (sketch, not code to run)

```
page = fetcher.fetch(listing_url)                     # FetchResult
if page.ok:
    analyze_listing(page.final_url, page.text, example_job_url).evidence   # job_links, spa_shell, notes
    analyze_vendors(page.final_url, page.text, (("example job URL", example_job_url),))
    find_feed_links(page.final_url, scan_html(page.text))                  # leads: fetch, then analyze_feed
job = fetcher.fetch(example_job_url)  ->  analyze_jobposting(job.final_url, job.text)
robots = fetcher.robots_rules(url)    ->  robots.sitemaps: fetch the FIRST one, then read_sitemap(...)
terms_from_fetch(fetcher.fetch(terms_url))            # also covers failed and blocked fetches
```

Every analyzer returns contract `Evidence` (kinds `job_links`, `spa_shell`, `vendor_host`,
`jsonld_jobposting`, `feed_link`, `feed_parsed`, `sitemap_jobs`, `note`) or a `TermsFlag`.
T4 decides the mode from the evidence with the spec's priority (feed, json-ld, json-api, vendor,
html, js-only, manual), builds `TriageResult.ok/failed/blocked/empty`, and lets the contract derive
the verdict. A `sitemap_line` is not a URL source; only `sitemap_jobs` counts. The terms flag never
changes the verdict.

## Open items

| Item | When |
|---|---|
| DONE 14 Nov 2026: `fetch(url, allow_truncated=True)` keeps the first 2 MB of a body and marks the result `truncated=True`; T4 uses it for the sitemap fetch only | done |
| Spec approval pending: the terms list rule (a prohibition in a lead-in sentence ending in ":" applies to the items under it, up to 25 sentences) | before T4 |
| CLI flags `--contact`, `--cache-dir`, `--replay`; one guard per firm; 90 s and 10 requests per firm; JSONL line per firm; `--resume`; the `api_url` note evidence; choosing the mode from the evidence | T4 |
| First live run: `urllib_transport` has never run against a real site | T6 |
| A real Spanish terms page: the saved "Normativa legal" page is only an index of links; the likely terms page is the footer link "Advertencias" | when the author has it |

## What is verified on real pages, and what is not

Real captures (local only, in `tests/local_fixtures/`): 3 listing pages, 2 sitemaps, 4 terms pages.

- Verified: the job-link shape rule (32, 72 and 100 links), the app-shell marker, vendor hosts on two
  pages, reading a real sitemap and an empty one, and three real bans on automated access (one French
  and two English; one written as a list, which exposed and fixed a gap), plus one real French terms
  page with no ban on automated access (a correct "not flagged"; it comes from a group's international
  site, not the careers site).
- NOT verified: JSON-LD on a real job page, feed reading (synthetic feeds only), a sitemap index,
  terms pages in Spanish, German, Dutch, Portuguese and Italian, and precision beyond one real "no ban"
  page. The language-guess thresholds (12% and 1.25) and the list reach (25 sentences, 500 characters)
  are first guesses, not tuned.

## Known limits

- Language prefixes in job URLs (`/en/` against `/fr/`) are not handled by the shape rule.
- Compressed sitemaps (`.xml.gz`) and `<loc>` tags with a namespace prefix are not read.
- A feed with a DOCTYPE or ENTITY declaration is refused with a note, on purpose.
- Only the id `root` is recognised as an app-shell container; others are added when a real page shows them.
- Terms text is read from the raw HTML only; a page whose text is built by JavaScript is `not_assessed`.
- `html.parser` can behave differently between Python patch versions (3.12.3 and 3.12.8 differed once):
  a pass on the author's machine is what confirms HTML-related tests.
