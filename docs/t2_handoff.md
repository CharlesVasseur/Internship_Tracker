# T2 hand-off (snapshot at the end of T2, 11 Nov 2026)

Status: T2 (HTTP layer, response cache, block detection) is done. 333 tests pass, all offline.
This note is a snapshot, not a second source of truth: the rules live in `docs/triage_contract.md`.
If the two ever disagree, the spec wins.

## What T2 delivers

| File | Job | Names later steps use |
|---|---|---|
| `decode.py` | bytes to text: gzip/deflate, 2 MB cap, `not_text`, charset fallback | `decode_body`, `DecodedBody` |
| `robots.py` | RFC 9309 matcher, Crawl-delay, Sitemap lines | `parse_robots`, `RobotsRules`, `wait_seconds`, `agent_token` |
| `transport.py` | one GET, typed network errors, User-Agent | `urllib_transport`, `build_user_agent`, `ContactRequired`, `RawResponse`, `TransportError` |
| `cache.py` | save every answer, replay later with no network | `ResponseCache`, `RecordingTransport`, `ReplayTransport`, `CacheMiss`, `CacheError` |
| `fetch_result.py` | the typed outcome of one fetch | `FetchResult` |
| `fetcher.py` | politeness, robots, retries, redirects, blocks | `Fetcher` |

Tests helpers (not product code): `tests/conftest.py` (blocks all network in tests),
`tests/fake_transport.py` (scripted fake transport and fake clock).

## How the pieces plug together (for T3 and T4)

```python
agent = build_user_agent(contact)                 # --contact or SURVEY_CONTACT; raises ContactRequired
cache = ResponseCache("triage_cache")             # --cache-dir
live = RecordingTransport(urllib_transport, cache)        # live run that saves everything
# replay run:  transport = ReplayTransport(cache), and pass sleep=lambda s: None to Fetcher
fetcher = Fetcher(live, agent)                    # one Fetcher per run: it remembers hosts and robots
result = fetcher.fetch(url)                       # FetchResult, never raises
rules = fetcher.robots_rules(url)                 # RobotsRules for that site, after a fetch
```

- `FetchResult.status` is `ok`, `blocked` or `failed`, with `text`, `error` or `block` set accordingly.
  Its `error` and `block` are the contract's own `TriageError` and `BlockInfo`.
- `FetchResult.requests` lists every URL requested during that fetch. `fetcher.request_log` lists all.
- A failed or blocked fetch must never be turned into "no openings" or "not monitorable" (spec, principle 2).

## What T3 gets from T2

- Page text with the charset used and any fallback note (`charset_used`, `charset_note`).
- Robots facts for evidence: `rules.describe()`, `rules.crawl_delay`, `rules.sitemaps`.
  The Sitemap lines are only recorded. T3 fetches a sitemap through `fetcher.fetch(...)`, so it obeys robots too.
- Not available yet: challenge-page markers (none written without a real captured page).

## Open items (not done in T2, by design)

| Item | Step |
|---|---|
| `urllib_transport` has **never run against a real site**. No test opens a connection. First live run: check that real exceptions match the typed kinds, and read the first output together | T6 |
| CLI flags `--contact`, `--cache-dir`, `--replay`; one guard per firm; 90 s budget per firm; 10 requests per firm; JSONL line per firm; `--resume` | T4 |
| `api_url` hint: add the `note` evidence "api_url provided, not checked in this version" | T4 |
| Sitemap fetching and the "job-like URLs" rule (needs the real Banque de France and AXA sitemap files) | T3 |
| Terms of use flag (item 10), vendor table, job-link counting, JSON-LD, feed detection | T3 |
| Challenge-page markers (need a real captured page) | T3 |
| ETag / Last-Modified and the 6-hour refresh limit from the Knowledge Base | later (monitoring, not triage) |

## Known limits

- Robots: percent-encoding is compared as written; only user-agent, allow, disallow, crawl-delay, sitemap are read.
- Robots rules are kept per site origin (http and https separately); rate limit and host stop are per host name.
- A site that answers `/robots.txt` with an HTML page shows as `failed/decode` (a human look), never as permission.
- A Crawl-delay over 10 s gives `failed/budget` (retryable). The cap is the constant `MAX_CRAWL_DELAY_SECONDS`.
- Only a 200 page with no robots check problem is read: a page that silently serves a CAPTCHA with HTTP 200 shows as `ok` until T3 has a real marker.
