"""The polite fetcher (step T2.4): one URL in, one typed FetchResult out.

It puts the earlier pieces together. For a URL it:

    1. checks the URL is http(s) with a host
    2. fetches /robots.txt of that site first (once per site) and obeys it
    3. waits: at least 1 second between requests to a host, or the Crawl-delay
    4. asks the transport, retrying only timeouts, connection/DNS errors and 5xx
       (2 retries, waiting 2 s then 4 s)
    5. stops the whole host on 401, 403 or 429 and never retries them
    6. follows redirects (max 5, loops detected); every new site gets its own
       robots.txt check first
    7. turns the body into text with decode.py

Nothing raises out of fetch(). A bug or a bad cache entry becomes
failed/internal. A failed or blocked fetch is never an answer about the firm.

Time is injected (`sleep`, `clock`) so tests run instantly and a replay run can
skip waiting. The network is injected too (`transport`): live, recording,
replay or a test fake.

Pure Python standard library. This file never opens a connection itself.
"""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable

from tracker.triage.contract import BlockInfo, TriageError
from tracker.triage.decode import decode_body
from tracker.triage.fetch_result import FetchResult
from tracker.triage.urls import resolve_url
from tracker.triage.robots import (
    MAX_CRAWL_DELAY_SECONDS,
    MIN_SECONDS_PER_HOST,
    RobotsRules,
    agent_token,
    parse_robots,
    wait_seconds,
)
from tracker.triage.transport import (
    TIMEOUT_SECONDS,
    RawResponse,
    Transport,
    TransportError,
    request_headers,
)

MAX_REDIRECTS = 5
MAX_RETRIES = 2
RETRY_WAITS = (2.0, 4.0)                              # before the 2nd and the 3rd attempt
RETRYABLE_TRANSPORT_KINDS = ("timeout", "connection", "dns")
BLOCKING_STATUSES = {401: "http_401", 403: "http_403", 429: "http_429"}
ROBOTS_UNREACHABLE_KINDS = ("timeout", "connection", "http_5xx")   # shown as robots_unreachable
SILENT_BLOCK_HINT = " (repeated timeouts: the site may be silently blocking us)"


@dataclass(frozen=True)
class _Problem:
    """Why a request did not give a usable answer: an error or a block."""

    error: TriageError | None = None
    block: BlockInfo | None = None


class Fetcher:
    def __init__(
        self,
        transport: Transport,
        user_agent: str,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = TIMEOUT_SECONDS,
        max_crawl_delay: float = MAX_CRAWL_DELAY_SECONDS,
    ) -> None:
        self._transport = transport
        self._headers = request_headers(user_agent)
        self._token = agent_token(user_agent)
        self._sleep = sleep
        self._clock = clock
        self._timeout = timeout
        self._max_crawl_delay = max_crawl_delay
        self._robots: dict[str, RobotsRules | _Problem] = {}   # by origin, e.g. https://x.test
        self._stopped: dict[str, _Problem] = {}                 # by host: refused us (401/403/429)
        self._last_request: dict[str, float] = {}               # by host: when we last asked
        self._log: list[str] = []                               # every URL requested, in order

    # -- public ---------------------------------------------------------------------

    @property
    def request_log(self) -> tuple[str, ...]:
        """Every URL requested so far (retries included). T4 uses it for budgets."""
        return tuple(self._log)

    def robots_rules(self, url: str) -> RobotsRules | None:
        """The robots rules already loaded for this URL's site, or None."""
        known = self._robots.get(_origin(url) or "")
        return known if isinstance(known, RobotsRules) else None

    def fetch(self, url: str, *, allow_truncated: bool = False) -> FetchResult:
        """Fetch one URL. Never raises.

        A body over the 2 MB cap is normally a failure (too_large). With
        allow_truncated=True the first 2 MB are read instead and the result says
        so (truncated=True): this is for sitemaps, where the start of the file is enough.
        """
        start = len(self._log)
        try:
            return self._fetch(url, start, allow_truncated)
        except Exception as exc:   # a bug or a broken cache entry: report it, keep the run alive
            error = TriageError.of("internal", f"{type(exc).__name__}: {exc}", url)
            return FetchResult(url, url, "failed", error=error, requests=tuple(self._log[start:]))

    # -- the page, with redirects ------------------------------------------------------

    def _fetch(self, url: str, start: int, allow_truncated: bool = False) -> FetchResult:
        current = url
        chain: list[str] = []
        visited = {url}

        for _ in range(MAX_REDIRECTS + 1):
            if _origin(current) is None:
                what = "the URL" if current == url else "a redirect target"
                return self._failed(url, current, chain, start, TriageError.of(
                    "invalid_url", f"{what} is not a usable http(s) address: {current!r}", current))

            answer = self._get_page(current)
            if isinstance(answer, _Problem):
                return self._from_problem(answer, url, current, chain, start)
            raw = answer

            if 300 <= raw.status < 400:
                location = raw.headers.get("location", "").strip()
                if not location:
                    return self._failed(url, current, chain, start, TriageError.of(
                        "redirect_loop", f"HTTP {raw.status} redirect without a Location header", current))
                target = resolve_url(current, location, drop_fragment=True)
                if target is None:
                    return self._failed(url, current, chain, start, TriageError.of(
                        "invalid_url", f"a redirect target is not a usable address: {location!r}", current))
                if target in visited:
                    return self._failed(url, current, chain, start, TriageError.of(
                        "redirect_loop", f"redirect loop: {target} was already visited", current))
                if len(chain) >= MAX_REDIRECTS:
                    return self._failed(url, current, chain, start, TriageError.of(
                        "redirect_loop", f"more than {MAX_REDIRECTS} redirects, stopped at {current}", current))
                chain.append(target)
                visited.add(target)
                current = target
                continue

            return self._finish(url, current, chain, raw, start, allow_truncated)

        return self._failed(url, current, chain, start, TriageError.of(
            "internal", "redirect loop ended without a result (bug)", current))

    def _finish(
        self, url: str, current: str, chain: list[str], raw: RawResponse, start: int,
        allow_truncated: bool = False,
    ) -> FetchResult:
        """An answer that is not a redirect: check the status, then read the body."""
        status = raw.status
        if 400 <= status < 500:
            return self._failed(url, current, chain, start, TriageError.of(
                "http_4xx", f"HTTP {status}: the URL is probably wrong or the page moved", current), status)
        if not 200 <= status < 300:
            return self._failed(url, current, chain, start, TriageError.of(
                "internal", f"unexpected HTTP status {status}", current), status)
        if raw.truncated and not allow_truncated:
            return self._failed(url, current, chain, start, TriageError.of(
                "too_large", "the body is larger than the size cap", current), status)
        decoded = decode_body(raw.body, raw.headers, partial=allow_truncated, wire_truncated=raw.truncated)
        if decoded.error_kind is not None:
            return self._failed(url, current, chain, start, TriageError.of(
                decoded.error_kind, decoded.error_message or decoded.error_kind, current), status)
        return FetchResult(
            url=url, final_url=current, status="ok", http_status=status,
            content_type=raw.headers.get("content-type", ""), text=decoded.text,
            charset_used=decoded.charset_used, charset_source=decoded.charset_source,
            charset_note=decoded.charset_note, redirect_chain=tuple(chain),
            requests=tuple(self._log[start:]),
            truncated=allow_truncated and (raw.truncated or decoded.truncated),
        )

    def _failed(
        self, url: str, current: str, chain: list[str], start: int,
        error: TriageError, http_status: int | None = None,
    ) -> FetchResult:
        return FetchResult(
            url=url, final_url=current, status="failed", http_status=http_status,
            redirect_chain=tuple(chain), error=error, requests=tuple(self._log[start:]),
        )

    def _from_problem(
        self, problem: _Problem, url: str, current: str, chain: list[str], start: int
    ) -> FetchResult:
        if problem.block is not None:
            return FetchResult(
                url=url, final_url=current, status="blocked", http_status=problem.block.http_status,
                redirect_chain=tuple(chain), block=problem.block, requests=tuple(self._log[start:]),
            )
        assert problem.error is not None
        return self._failed(url, current, chain, start, problem.error)

    # -- one page request: host stop, robots, delay ---------------------------------------

    def _get_page(self, url: str) -> RawResponse | _Problem:
        host = _hostname(url)
        if host in self._stopped:
            return self._stopped[host]
        rules = self._rules_for(url)
        if isinstance(rules, _Problem):
            return rules
        if not rules.allowed(url):
            return _Problem(block=BlockInfo("robots_disallow", url, None))
        interval, over_cap = wait_seconds(rules.crawl_delay, cap=self._max_crawl_delay)
        if over_cap:
            return _Problem(error=TriageError.of(
                "budget",
                f"robots.txt asks for a Crawl-delay of {rules.crawl_delay:g}s, over the "
                f"{self._max_crawl_delay:g}s limit; not waiting. Raise the limit to fetch this site.",
                url))
        return self._request(url, interval)

    # -- robots.txt ---------------------------------------------------------------------------

    def _rules_for(self, url: str) -> RobotsRules | _Problem:
        origin = _origin(url) or ""
        known = self._robots.get(origin)
        if known is None:
            known = self._load_robots(origin)
            self._robots[origin] = known          # a problem is remembered too: no hammering
        return known

    def _load_robots(self, origin: str) -> RobotsRules | _Problem:
        robots_url = origin + "/robots.txt"
        answer = self._request_following(robots_url)
        if isinstance(answer, _Problem):
            return _robots_problem(answer)
        raw = answer
        if raw.status in (404, 410):
            return RobotsRules.allow_all()        # no robots.txt means no rules
        if 400 <= raw.status < 500:
            return _Problem(error=TriageError.of(
                "http_4xx", f"robots.txt answered HTTP {raw.status}", robots_url))
        if not 200 <= raw.status < 300:
            return _Problem(error=TriageError.of(
                "internal", f"robots.txt: unexpected HTTP status {raw.status}", robots_url))
        if raw.truncated:
            return _Problem(error=TriageError.of(
                "too_large", "robots.txt is larger than the size cap", robots_url))
        decoded = decode_body(raw.body, raw.headers)
        if decoded.error_kind is not None:
            return _Problem(error=TriageError.of(
                decoded.error_kind, f"robots.txt: {decoded.error_message}", robots_url))
        rules = parse_robots(decoded.text or "", self._token)
        if not rules.readable:
            return _Problem(error=TriageError.of(
                "decode", f"robots.txt could not be read as robots rules: {'; '.join(rules.notes)}",
                robots_url))
        return rules

    def _request_following(self, url: str) -> RawResponse | _Problem:
        """Fetch robots.txt, following redirects. No robots check on the way: it IS robots."""
        visited = {url}
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            answer = self._request(current, MIN_SECONDS_PER_HOST)
            if isinstance(answer, _Problem) or not 300 <= answer.status < 400:
                return answer
            location = answer.headers.get("location", "").strip()
            target = (resolve_url(current, location, drop_fragment=True) or "") if location else ""
            if not target or _origin(target) is None:
                return _Problem(error=TriageError.of(
                    "redirect_loop", f"robots.txt: unusable redirect (HTTP {answer.status})", current))
            if target in visited or len(visited) > MAX_REDIRECTS:
                return _Problem(error=TriageError.of(
                    "redirect_loop", f"robots.txt: redirect loop or too many redirects at {target}", current))
            visited.add(target)
            current = target
        return _Problem(error=TriageError.of("internal", "robots redirect loop ended without a result", url))

    # -- one request with waiting, retries and the 401/403/429 stop ---------------------------

    def _request(self, url: str, interval: float) -> RawResponse | _Problem:
        host = _hostname(url)
        if host in self._stopped:
            return self._stopped[host]
        attempts = MAX_RETRIES + 1
        last_kind, last_message = "internal", "no attempt was made"

        for attempt in range(attempts):
            if attempt > 0:
                self._sleep(RETRY_WAITS[attempt - 1])
            self._wait_turn(host, interval)
            self._log.append(url)
            try:
                raw = self._transport(url, self._headers, self._timeout)
            except TransportError as error:
                if error.kind not in RETRYABLE_TRANSPORT_KINDS:     # tls, invalid_url: one attempt only
                    return _Problem(error=TriageError.of(error.kind, error.message, url))
                last_kind, last_message = error.kind, error.message
                continue

            if raw.status in BLOCKING_STATUSES:                     # never retried, host stopped
                problem = _Problem(block=BlockInfo(BLOCKING_STATUSES[raw.status], url, raw.status))
                self._stopped[host] = problem
                return problem
            if 500 <= raw.status < 600:
                last_kind, last_message = "http_5xx", f"HTTP {raw.status}"
                continue
            return raw

        message = f"{last_message} (after {attempts} attempts)"
        if last_kind == "timeout":
            message += SILENT_BLOCK_HINT
        return _Problem(error=TriageError.of(last_kind, message, url))

    def _wait_turn(self, host: str, interval: float) -> None:
        """Wait until `interval` seconds have passed since the last request to this host."""
        now = self._clock()
        last = self._last_request.get(host)
        if last is not None and interval - (now - last) > 0:
            self._sleep(interval - (now - last))
        self._last_request[host] = self._clock()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _origin(url: str) -> str | None:
    """'https://host[:port]' for a usable http(s) URL, else None."""
    try:
        parts = urllib.parse.urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def _hostname(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _robots_problem(problem: _Problem) -> _Problem:
    """Show a robots.txt that could not be fetched the way the contract names it."""
    error = problem.error
    if error is not None and error.kind in ROBOTS_UNREACHABLE_KINDS:
        return _Problem(error=TriageError.of(
            "robots_unreachable", f"robots.txt could not be fetched: {error.message}", error.url))
    return problem
