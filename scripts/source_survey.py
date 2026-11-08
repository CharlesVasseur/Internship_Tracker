"""Step 0d: fill one survey Markdown file per firm from survey_input.csv.

Built in small steps: template renderer, polite HTTP client, parsers, then the
per-firm survey and command line. Nothing is fetched without --live.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import html as html_lib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

# Replace <MY_EMAIL> before a live run. Tests may inject another User-Agent.
CONTACT_PLACEHOLDER = "<MY_EMAIL>"
USER_AGENT = (
    "InternshipTracker/0.1 (source survey; contact " + CONTACT_PLACEHOLDER + ")"
)

CSV_COLUMNS = [
    "firm",
    "careers_entry_url",
    "listing_url",
    "example_job_url",
    "example_job_title",
    "terms_url",
]

OPTIONAL_COLUMNS = ["country"]

MANUAL = "MANUAL: to be filled by Charles"

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO_ROOT / "docs" / "survey_template.md"

LIVE_EMAIL_MESSAGE = (
    "Live run refused: USER_AGENT still contains "
    f"{CONTACT_PLACEHOLDER!r}. Replace it with a real contact address: "
    "pass --contact you@example.com (or set SURVEY_CONTACT)."
)

MAX_BODY_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 15
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = (2.0, 4.0)
MIN_SECONDS_PER_HOST = 1.0
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
BLOCKED_STATUSES = {403, 429}
MAX_SAME_HOST_REDIRECTS = 10

# transport(url, headers) -> RawResponse; may raise TimeoutError
Transport = Callable[[str, dict[str, str]], "RawResponse"]


def require_live_user_agent(user_agent: str) -> None:
    """Exit if a live run still uses the email placeholder. Tests inject their own."""
    if CONTACT_PLACEHOLDER in user_agent:
        raise SystemExit(LIVE_EMAIL_MESSAGE)


def origin_of(url: str) -> str:
    """scheme://netloc for robots and per-host rate limits."""
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def hostname_of(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").casefold()
    return host


def robots_url_for(url: str) -> str:
    return origin_of(url).rstrip("/") + "/robots.txt"


def extract_star_block(robots_text: str) -> str:
    """Return User-agent: * records (plus their rules), not other agents."""
    blocks: list[str] = []
    current: list[str] = []
    in_star = False
    for raw in robots_text.splitlines():
        stripped = raw.strip()
        lower = stripped.lower()
        if lower.startswith("user-agent:"):
            if in_star and current:
                blocks.append("\n".join(current))
            agent = stripped.split(":", 1)[1].strip()
            in_star = agent == "*"
            current = [stripped] if in_star else []
            continue
        if lower.startswith("sitemap:"):
            continue
        if in_star:
            if stripped == "":
                if current:
                    blocks.append("\n".join(current))
                in_star = False
                current = []
            else:
                current.append(stripped)
    if in_star and current:
        blocks.append("\n".join(current))
    return "\n\n".join(blocks)


def extract_sitemap_lines(robots_text: str) -> list[str]:
    found: list[str] = []
    for raw in robots_text.splitlines():
        stripped = raw.strip()
        if stripped.lower().startswith("sitemap:"):
            found.append(stripped)
    return found


def decode_http_body(body: bytes, content_type: str) -> tuple[str | None, str | None]:
    """Decode with charset from Content-Type, else UTF-8. Never guess latin-1."""
    charset = "utf-8"
    match = re.search(r"charset\s*=\s*([^\s;]+)", content_type, flags=re.I)
    if match:
        charset = match.group(1).strip().strip("\"'")
    try:
        return body.decode(charset), None
    except (LookupError, UnicodeDecodeError) as exc:
        return None, f"failed: decode error ({exc})"


def read_capped(stream, limit: int = MAX_BODY_BYTES) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    while True:
        piece = stream.read(64 * 1024)
        if not piece:
            return b"".join(chunks), False
        if total + len(piece) > limit:
            chunks.append(piece[: limit - total])
            return b"".join(chunks), True
        chunks.append(piece)
        total += len(piece)


@dataclass
class RawResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    truncated: bool = False


@dataclass
class RobotsRecord:
    origin: str
    robots_url: str
    status: int | None
    text: str
    outcome: str
    star_block: str
    sitemap_lines: list[str]
    parser: urllib.robotparser.RobotFileParser | None = None


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None
    headers: dict[str, str]
    body: bytes
    text: str | None
    outcome: str
    redirect_target: str | None = None
    decode_error: str | None = None
    truncated: bool = False
    robots: RobotsRecord | None = None

    @property
    def ok(self) -> bool:
        """True only for a 2xx response whose body was decoded."""
        return (
            self.outcome == "ok"
            and self.text is not None
            and self.status is not None
            and 200 <= self.status <= 299
        )


def urllib_transport(url: str, headers: dict[str, str]) -> RawResponse:
    """Live GET that does not follow redirects. Unused in tests."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
    )
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            body, truncated = read_capped(response)
            header_map = {k.lower(): v for k, v in response.headers.items()}
            status = getattr(response, "status", None) or response.getcode()
            return RawResponse(int(status), header_map, body, truncated)
    except urllib.error.HTTPError as err:
        body, truncated = read_capped(err)
        header_map = {}
        if err.headers is not None:
            header_map = {k.lower(): v for k, v in err.headers.items()}
        return RawResponse(int(err.code), header_map, body, truncated)
    except TimeoutError as exc:
        raise TimeoutError("timeout") from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError) or "timed out" in str(exc).lower():
            raise TimeoutError("timeout") from exc
        raise


class HttpClient:
    """Polite GET: robots first per host, same-host redirects, size cap, retries."""

    def __init__(
        self,
        user_agent: str,
        transport: Transport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        min_interval: float = MIN_SECONDS_PER_HOST,
    ) -> None:
        self.user_agent = user_agent
        self.transport = transport or urllib_transport
        self.sleeper = sleeper
        self.monotonic = monotonic
        self.min_interval = min_interval
        self._last_request_at: dict[str, float] = {}
        self._cache: dict[str, FetchResult] = {}
        self._robots: dict[str, RobotsRecord] = {}
        self._blocked: set[str] = set()
        self._request_log: list[str] = []

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent}

    @property
    def request_log(self) -> list[str]:
        """Every URL requested so far, in order (a copy)."""
        return list(self._request_log)

    def _wait_for_host(self, origin: str) -> None:
        last = self._last_request_at.get(origin)
        if last is None:
            return
        wait = self.min_interval - (self.monotonic() - last)
        if wait > 0:
            self.sleeper(wait)

    def _raw_get(self, url: str) -> RawResponse | FetchResult:
        """GET with rate limit and retries. Returns FetchResult on hard failure."""
        origin = origin_of(url)
        if origin in self._blocked:
            return FetchResult(
                url=url,
                final_url=url,
                status=None,
                headers={},
                body=b"",
                text=None,
                outcome="blocked",
            )

        last_error = "failed: unknown"
        attempts = MAX_RETRIES + 1
        for attempt in range(attempts):
            self._wait_for_host(origin)
            self._last_request_at[origin] = self.monotonic()
            self._request_log.append(url)
            try:
                raw = self.transport(url, self._headers())
            except TimeoutError:
                last_error = "failed: timeout"
                if attempt < MAX_RETRIES:
                    self.sleeper(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                return FetchResult(
                    url=url,
                    final_url=url,
                    status=None,
                    headers={},
                    body=b"",
                    text=None,
                    outcome=last_error,
                )
            except Exception as exc:
                last_error = f"failed: {type(exc).__name__}: {exc}"
                if attempt < MAX_RETRIES:
                    self.sleeper(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                return FetchResult(
                    url=url,
                    final_url=url,
                    status=None,
                    headers={},
                    body=b"",
                    text=None,
                    outcome=last_error,
                )

            if raw.status in BLOCKED_STATUSES:
                self._blocked.add(origin)
                return FetchResult(
                    url=url,
                    final_url=url,
                    status=raw.status,
                    headers=raw.headers,
                    body=raw.body,
                    text=None,
                    outcome="blocked",
                )
            if 500 <= raw.status <= 599 and attempt < MAX_RETRIES:
                self.sleeper(RETRY_BACKOFF_SECONDS[min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)])
                continue
            return raw

        return FetchResult(
            url=url,
            final_url=url,
            status=None,
            headers={},
            body=b"",
            text=None,
            outcome=last_error,
        )

    def _finish(self, url: str, raw: RawResponse, final_url: str) -> FetchResult:
        if raw.truncated:
            return FetchResult(
                url=url,
                final_url=final_url,
                status=raw.status,
                headers=raw.headers,
                body=raw.body,
                text=None,
                truncated=True,
                outcome="failed: response larger than 2 MB",
            )
        content_type = raw.headers.get("content-type", "")
        text, decode_error = decode_http_body(raw.body, content_type)
        if decode_error:
            return FetchResult(
                url=url,
                final_url=final_url,
                status=raw.status,
                headers=raw.headers,
                body=raw.body,
                text=None,
                decode_error=decode_error,
                outcome=decode_error,
            )
        return FetchResult(
            url=url,
            final_url=final_url,
            status=raw.status,
            headers=raw.headers,
            body=raw.body,
            text=text,
            outcome="ok",
        )

    def load_robots(self, page_url: str) -> RobotsRecord:
        """Fetch and parse robots.txt for this URL's origin (once per origin)."""
        origin = origin_of(page_url)
        cached = self._robots.get(origin)
        if cached is not None:
            return cached

        if origin in self._blocked:
            record = RobotsRecord(
                origin=origin,
                robots_url=robots_url_for(page_url),
                status=None,
                text="",
                outcome="blocked",
                star_block="",
                sitemap_lines=[],
            )
            self._robots[origin] = record
            return record

        robots_url = robots_url_for(page_url)
        fetched = self.get(robots_url, check_robots=False)
        if fetched.outcome == "blocked":
            record = RobotsRecord(
                origin=origin,
                robots_url=robots_url,
                status=fetched.status,
                text="",
                outcome="blocked",
                star_block="",
                sitemap_lines=[],
            )
            self._robots[origin] = record
            return record
        if fetched.outcome != "ok" and fetched.status != 404:
            record = RobotsRecord(
                origin=origin,
                robots_url=robots_url,
                status=fetched.status,
                text="",
                outcome=(
                    fetched.outcome
                    if fetched.outcome.startswith("failed")
                    or fetched.outcome == "cross-host redirect not followed"
                    else f"failed: robots.txt {fetched.outcome}"
                ),
                star_block="",
                sitemap_lines=[],
            )
            self._robots[origin] = record
            return record
        if fetched.status == 404:
            record = RobotsRecord(
                origin=origin,
                robots_url=robots_url,
                status=404,
                text="",
                outcome="no robots.txt (404)",
                star_block="",
                sitemap_lines=[],
                parser=None,
            )
            self._robots[origin] = record
            return record
        if fetched.status != 200 or fetched.text is None:
            record = RobotsRecord(
                origin=origin,
                robots_url=robots_url,
                status=fetched.status,
                text="",
                outcome=fetched.outcome
                if fetched.outcome.startswith("failed")
                else f"failed: robots.txt HTTP {fetched.status}",
                star_block="",
                sitemap_lines=[],
            )
            self._robots[origin] = record
            return record

        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(fetched.text.splitlines())
        record = RobotsRecord(
            origin=origin,
            robots_url=robots_url,
            status=200,
            text=fetched.text,
            outcome="ok",
            star_block=extract_star_block(fetched.text),
            sitemap_lines=extract_sitemap_lines(fetched.text),
            parser=parser,
        )
        self._robots[origin] = record
        return record

    def allowed_by_robots(self, url: str, robots: RobotsRecord) -> bool:
        """True if this User-Agent may fetch the URL given a loaded robots record."""
        if robots.outcome == "no robots.txt (404)":
            return True
        if robots.outcome == "blocked":
            return False
        if robots.outcome != "ok" or robots.parser is None:
            return False
        return robots.parser.can_fetch(self.user_agent, url)

    def get(self, url: str, *, check_robots: bool = True) -> FetchResult:
        """GET a URL. Reads robots.txt for its host first unless this is robots.txt."""
        if url in self._cache:
            return self._cache[url]

        current = url
        hops = 0
        robots_for_start: RobotsRecord | None = None

        while hops <= MAX_SAME_HOST_REDIRECTS:
            origin = origin_of(current)
            if origin in self._blocked:
                result = FetchResult(
                    url=url,
                    final_url=current,
                    status=None,
                    headers={},
                    body=b"",
                    text=None,
                    outcome="blocked",
                    robots=self._robots.get(origin),
                )
                self._cache[url] = result
                return result

            is_robots = urllib.parse.urlparse(current).path == "/robots.txt"
            if check_robots and not is_robots:
                robots = self.load_robots(current)
                if robots_for_start is None:
                    robots_for_start = robots
                if robots.outcome == "blocked":
                    result = FetchResult(
                        url=url,
                        final_url=current,
                        status=robots.status,
                        headers={},
                        body=b"",
                        text=None,
                        outcome="blocked",
                        robots=robots,
                    )
                    self._cache[url] = result
                    return result
                if robots.outcome not in ("ok", "no robots.txt (404)"):
                    result = FetchResult(
                        url=url,
                        final_url=current,
                        status=robots.status,
                        headers={},
                        body=b"",
                        text=None,
                        outcome=robots.outcome,
                        robots=robots,
                    )
                    self._cache[url] = result
                    return result
                if not self.allowed_by_robots(current, robots):
                    result = FetchResult(
                        url=url,
                        final_url=current,
                        status=None,
                        headers={},
                        body=b"",
                        text=None,
                        outcome="disallowed",
                        robots=robots,
                    )
                    self._cache[url] = result
                    return result

            raw_or_fail = self._raw_get(current)
            if isinstance(raw_or_fail, FetchResult):
                raw_or_fail.robots = robots_for_start
                self._cache[url] = raw_or_fail
                return raw_or_fail

            raw = raw_or_fail
            if raw.status in REDIRECT_STATUSES:
                location = raw.headers.get("location", "")
                if not location:
                    result = FetchResult(
                        url=url,
                        final_url=current,
                        status=raw.status,
                        headers=raw.headers,
                        body=raw.body,
                        text=None,
                        outcome="failed: redirect without Location",
                        robots=robots_for_start,
                    )
                    self._cache[url] = result
                    return result
                target = urllib.parse.urljoin(current, location)
                if hostname_of(target) != hostname_of(current):
                    result = FetchResult(
                        url=url,
                        final_url=current,
                        status=raw.status,
                        headers=raw.headers,
                        body=raw.body,
                        text=None,
                        outcome="cross-host redirect not followed",
                        redirect_target=target,
                        robots=robots_for_start,
                    )
                    self._cache[url] = result
                    return result
                current = target
                hops += 1
                continue

            result = self._finish(url, raw, current)
            result.robots = robots_for_start
            self._cache[url] = result
            return result

        result = FetchResult(
            url=url,
            final_url=current,
            status=None,
            headers={},
            body=b"",
            text=None,
            outcome="failed: too many same-host redirects",
            robots=robots_for_start,
        )
        self._cache[url] = result
        return result


# ---------------------------------------------------------------------------
# Step 3: parsers. Pure functions: text in, facts out. No network here.
# Rule for all of them: "no" / "not found" is only returned when the input was
# parsed correctly. Anything unexpected becomes "failed: <reason>".
# ---------------------------------------------------------------------------


def page_problem(result: FetchResult) -> str | None:
    """Return None if the fetched page can be parsed as HTML, else the reason.

    The reason is one of: "blocked", "disallowed", "failed: ...". Callers write
    that reason into the survey file instead of "no".
    """
    content_type = result.headers.get("content-type", "").lower()
    is_html = content_type.startswith("text/html") or "xhtml" in content_type
    if result.outcome.startswith("failed: decode error") and content_type and not is_html:
        # A PDF fails to decode as text; the real reason is the file type.
        return f"failed: not HTML ({content_type.split(';')[0].strip()})"
    if result.outcome != "ok":
        return result.outcome
    if result.status is None or not 200 <= result.status <= 299:
        return f"failed: HTTP {result.status}"
    if result.text is None:
        return "failed: no body"
    if content_type and not is_html:
        return f"failed: not HTML ({content_type.split(';')[0].strip()})"
    return None


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalise_for_match(text: str) -> str:
    """Unescape entities, drop accents, lower-case, collapse whitespace."""
    text = html_lib.unescape(text)
    text = strip_accents(text).casefold()
    return re.sub(r"\s+", " ", text).strip()


class _TagCollector(HTMLParser):
    """Collect every start tag with its attributes (names lower-cased)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag, attrs) -> None:
        self.tags.append((tag, {k.lower(): (v or "") for k, v in attrs}))


def _collect_tags(html_text: str) -> list[tuple[str, dict[str, str]]]:
    collector = _TagCollector()
    collector.feed(html_text)
    collector.close()
    return collector.tags


# ---- Item 4: JSON-LD JobPosting -------------------------------------------


class _JsonLdCollector(HTMLParser):
    """Collect the text of every <script type="application/ld+json">."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._buffer: list[str] | None = None

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "script":
            kind = (dict(attrs).get("type") or "").lower()
            if kind.split(";")[0].strip() == "application/ld+json":
                self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._buffer is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag) -> None:
        if tag == "script" and self._buffer is not None:
            self.blocks.append("".join(self._buffer))
            self._buffer = None


def _is_jobposting(node: dict) -> bool:
    """@type may be a string or a list, plain or prefixed (schema:JobPosting)."""
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    for item in kinds:
        if isinstance(item, str):
            name = item.rstrip("/").split("/")[-1].split(":")[-1]
            if name == "JobPosting":
                return True
    return False


def _find_jobposting(node):
    """Search dicts and lists recursively (covers @graph and top-level arrays)."""
    if isinstance(node, dict):
        if _is_jobposting(node):
            return node
        for value in node.values():
            found = _find_jobposting(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_jobposting(item)
            if found is not None:
                return found
    return None


def find_jobposting_jsonld(html_text: str) -> tuple[str, str]:
    """Return (status, excerpt) for the example job page.

    status is "yes", "no" or "failed: invalid JSON-LD (...)".
    The excerpt is the first 30 lines of the JobPosting object itself (more
    useful than the whole block when @graph lists other objects first).
    A broken block is only "failed" if no other block holds a JobPosting,
    because the broken block might have been the one that contained it.
    """
    collector = _JsonLdCollector()
    collector.feed(html_text)
    collector.close()
    errors: list[str] = []
    for block in collector.blocks:
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError as exc:
            errors.append(f"line {exc.lineno} column {exc.colno}: {exc.msg}")
            continue
        posting = _find_jobposting(data)
        if posting is not None:
            lines = json.dumps(posting, indent=2, ensure_ascii=False).splitlines()
            return "yes", "\n".join(lines[:30])
    if errors:
        return f"failed: invalid JSON-LD ({errors[0]})", ""
    return "no", ""


# ---- Item 5: RSS / Atom links ---------------------------------------------


def find_feed_links(html_text: str, base_url: str) -> list[str]:
    """<link rel="alternate"> tags that look like RSS or Atom. No guessed paths."""
    feeds: list[str] = []
    for tag, attrs in _collect_tags(html_text):
        if tag != "link":
            continue
        if "alternate" not in attrs.get("rel", "").lower().split():
            continue
        href = attrs.get("href", "")
        if not href:
            continue
        kind = attrs.get("type", "").lower()
        path = urllib.parse.urlparse(href).path.lower()
        if "rss" in kind or "atom" in kind or path.endswith((".rss", ".atom")):
            absolute = urllib.parse.urljoin(base_url, href)
            if absolute not in feeds:
                feeds.append(absolute)
    return feeds


# ---- Item 6: recruiting-system clues --------------------------------------

VENDOR_NAMES = (
    "workday",
    "smartrecruiters",
    "greenhouse",
    "ever",
    "taleo",
    "oraclecloud",
    "eightfold",
    "phenom",
    "teamtailor",
    "workable",
    "personio",
    "recruitee",
    "ashby",
)


def _hosts_from_tags(
    tags: list[tuple[str, dict[str, str]]], base_url: str, only_links: bool
) -> set[str]:
    hosts: set[str] = set()
    for tag, attrs in tags:
        if only_links:
            candidates = [attrs.get("href", "")] if tag == "a" else []
        else:
            candidates = [attrs.get(k, "") for k in ("href", "src", "action")]
        for value in candidates:
            if not value:
                continue
            absolute = urllib.parse.urljoin(base_url, value)
            parsed = urllib.parse.urlparse(absolute)
            if parsed.scheme in ("http", "https") and parsed.hostname:
                hosts.add(parsed.hostname.casefold())
    return hosts


def all_referenced_hosts(html_text: str, base_url: str) -> set[str]:
    """Hostnames in href, src and form-action attributes (not in page text)."""
    return _hosts_from_tags(_collect_tags(html_text), base_url, only_links=False)


def outbound_link_hosts(html_text: str, base_url: str) -> list[str]:
    """Hostnames of <a href> links that leave the page's own host, sorted."""
    own = hostname_of(base_url)
    hosts = _hosts_from_tags(_collect_tags(html_text), base_url, only_links=True)
    return sorted(host for host in hosts if host != own)


def find_vendor_hits(hosts) -> dict[str, list[str]]:
    """Map vendor name -> hostnames that contain it.

    Only hostnames are searched, never page text, so "however" cannot match
    "ever". "ever" is a common word, so it needs a hostname label that is
    exactly "ever". Everything else is a substring of a label (for example
    "workday" matches "myworkdayjobs"). This is a clue, never proof.
    """
    hits: dict[str, list[str]] = {}
    for host in sorted(hosts):
        labels = host.split(".")
        for vendor in VENDOR_NAMES:
            if vendor == "ever":
                matched = vendor in labels
            else:
                matched = any(vendor in label for label in labels)
            if matched:
                hits.setdefault(vendor, []).append(host)
    return hits


def vendor_conclusion(hits: dict[str, list[str]]) -> str:
    """Only "likely" or "unknown". "confirmed" is Charles's call."""
    return "likely" if hits else "unknown"


# ---- Item 7a: is the job title in the raw HTML? ---------------------------


def title_in_html(title: str, html_text: str) -> bool | None:
    """True/False if the title appears in the raw HTML; None if no title given.

    Both sides are normalised (entities, accents, case, spaces), so "&amp;"
    and "é" do not cause a false "no". Tags are not stripped on purpose:
    stripping could glue the end of one list item to the start of the next.
    """
    needle = normalise_for_match(title)
    if not needle:
        return None
    return needle in normalise_for_match(html_text)


# ---- Item 8: obstacle hints (hints only, never a verdict) ------------------


def find_obstacle_hints(html_text: str) -> list[str]:
    """CAPTCHA keyword and password field. Cookie walls are NOT scripted:
    the word "cookie" is on almost every page, so it would be noise."""
    hints: list[str] = []
    if "captcha" in html_text.lower():
        hints.append("CAPTCHA keyword in HTML (hint only)")
    for tag, attrs in _collect_tags(html_text):
        if tag == "input" and attrs.get("type", "").lower() == "password":
            hints.append("password input in HTML: possible login form (hint only)")
            break
    return hints


# ---- Item 10: terms of use -------------------------------------------------

MIN_TERMS_TEXT_CHARS = 500
MAX_SENTENCE_CHARS = 400
MAX_TERMS_SENTENCES = 2

# Matched against accent-stripped, case-folded text, so patterns have no accents.
AUTOMATION_PATTERN = re.compile(
    r"\b(?:"
    # words that mean bots in several languages
    r"robots?|bots?|spiders?|\w*crawl\w*|scraping|scrapers?|scrape[sd]?|"
    r"roboter\w*|moissonn\w*|aranas?|rastre\w+|"
    # English
    r"automated\s+(?:means|access|tools?|systems?|scripts?|software|queries|programs?)|"
    # French
    r"(?:extraction|collecte|acces|outils?|procedes?|logiciels?|moyens?)"
    r"\s+(?:de\s+)?automatis\w*|"
    # Spanish
    r"(?:medios?|herramientas?|acceso|extraccion|procedimientos?|sistemas?|programas?)"
    r"\s+(?:de\s+)?automati[cz]\w*|"
    # German
    r"automati(?:sch|siert)\w*\s+(?:\w+\s+)?"
    r"(?:abruf\w*|zugriff\w*|abfrage\w*|auslesen\w*|programme?|verfahren|mittel|tools?|systeme?)|"
    # Dutch
    r"geautomatiseerde?\s+(?:\w+\s+)?"
    r"(?:toegang|middelen|systemen|tools?|programma\w*|software|verzoeken)"
    r")\b",
    re.IGNORECASE,
)

# Rough language guess from common words. Charles verifies it.
_STOPWORDS = {
    "English": {"the", "and", "of", "to", "you", "your", "may", "not", "is", "by", "with"},
    "French": {"le", "les", "des", "et", "du", "est", "vous", "pas", "sur", "par", "ou"},
    "Spanish": {"el", "los", "las", "del", "por", "con", "para", "una", "su", "o"},
    "German": {"der", "die", "das", "und", "nicht", "sie", "ist", "mit", "von", "zu", "den"},
    "Dutch": {"de", "het", "een", "van", "niet", "u", "te", "voor", "met", "zijn", "op"},
}


def guess_language(text: str) -> str:
    """Best-scoring language, or "unknown" if there is no clear winner."""
    words = re.findall(r"[a-z]+", strip_accents(text).casefold())
    scores = sorted(
        ((sum(1 for w in words if w in stop), name) for name, stop in _STOPWORDS.items()),
        reverse=True,
    )
    best_score, best_name = scores[0]
    if best_score < 2 or best_score == scores[1][0]:
        return "unknown"
    return best_name


class _TextCollector(HTMLParser):
    """Visible text of a page, with a line break at block-level tags."""

    SKIP = {"script", "style", "noscript", "template"}
    BREAK = {"p", "div", "li", "br", "tr", "td", "section", "article",
             "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag) -> None:
        if tag in self.SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def extract_visible_text(html_text: str) -> str:
    collector = _TextCollector()
    collector.feed(html_text)
    collector.close()
    return "".join(collector.parts)


def split_sentences(text: str) -> list[str]:
    pieces = re.split(r"(?<=[.!?;])\s+|\n+", text)
    cleaned = (re.sub(r"\s+", " ", piece).strip() for piece in pieces)
    return [piece for piece in cleaned if piece]


@dataclass
class TermsResult:
    status: str  # "yes", "no" or "failed: ..."
    sentences: list[tuple[str, str]]  # (sentence, guessed language)
    total_matches: int = 0


def assess_terms(html_text: str) -> TermsResult:
    """Look for sentences about automated access on a terms-of-use page.

    A page with almost no text is probably built by JavaScript, so we cannot
    say "no": it is reported as failed instead.
    """
    text = extract_visible_text(html_text)
    if len(text) < MIN_TERMS_TEXT_CHARS:
        return TermsResult(
            f"failed: terms page has only {len(text)} characters of text "
            "(JavaScript-rendered?)",
            [],
        )
    matches = [
        sentence
        for sentence in split_sentences(text)
        if AUTOMATION_PATTERN.search(strip_accents(sentence))
    ]
    if not matches:
        return TermsResult("no", [])
    shown = []
    for sentence in matches[:MAX_TERMS_SENTENCES]:
        if len(sentence) > MAX_SENTENCE_CHARS:
            sentence = sentence[:MAX_SENTENCE_CHARS].rstrip() + " [...]"
        shown.append((sentence, guess_language(sentence)))
    return TermsResult("yes", shown, len(matches))


def firm_slug(name: str) -> str:
    """Turn a firm name into a filename-safe slug (accents stripped)."""
    stripped = unicodedata.normalize("NFKD", name.strip())
    without_marks = "".join(
        ch for ch in stripped if not unicodedata.combining(ch)
    )
    hyphenated = re.sub(r"[^a-z0-9]+", "-", without_marks.casefold())
    return hyphenated.strip("-")


def load_survey_rows(csv_path: Path) -> list[dict[str, str]]:
    """Read semicolon-separated UTF-8 CSV and require the exact header."""
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        if reader.fieldnames is None:
            raise ValueError("CSV has no header row")
        header = [name.strip() for name in reader.fieldnames]
        if header not in (CSV_COLUMNS, CSV_COLUMNS + OPTIONAL_COLUMNS):
            raise ValueError(
                "CSV header must be exactly: " + ", ".join(CSV_COLUMNS)
                + " (optional last column: " + ", ".join(OPTIONAL_COLUMNS) + ")"
            )
        rows = []
        for raw in reader:
            rows.append({key: (raw.get(key) or "").strip() for key in header})
        return rows


@dataclass
class SurveyFields:
    """Values written into docs/survey_template.md slots. Step 1 leaves most empty."""

    firm_name: str
    country: str = MANUAL
    checked_on: str = MANUAL
    careers_entry_url: str = ""
    listing_url: str = ""
    example_job_url: str = ""
    example_job_title: str = ""
    listing_status: str = MANUAL
    listing_final_url: str = MANUAL
    robots_host: str = MANUAL
    robots_result: str = MANUAL
    robots_paste: str = MANUAL
    jsonld_status: str = MANUAL
    jsonld_excerpt: str = MANUAL
    rss_sitemap: str = MANUAL
    job_page_domain: str = MANUAL
    apply_domains: str = MANUAL
    vendors_found: str = MANUAL
    conclusion: str = "unknown"
    title_in_source: str = MANUAL
    xhr_note: str = MANUAL
    obstacles: str = MANUAL
    notes: str = MANUAL
    terms_url: str = ""
    terms_mentions: str = MANUAL
    terms_sentences: str = MANUAL
    extra_hosts_note: str = ""


def _indent_block(text: str, prefix: str = "     ") -> str:
    """Indent a multi-line paste so it sits under a template bullet."""
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(prefix + line if line else prefix.rstrip() for line in lines)


def render_survey(fields: SurveyFields) -> str:
    """Fill Charles's template in place. Labels and spacing stay as in the file."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    expected_start = "FIRM: <name>, <country>          Checked on: <date>"
    if not template.startswith(expected_start):
        raise ValueError(
            "docs/survey_template.md does not match the expected first line"
        )

    host_line = f"{fields.robots_host}/robots.txt"
    if fields.extra_hosts_note:
        host_line = f"{host_line} ({fields.extra_hosts_note})"

    filled = template
    filled = filled.replace("<name>", fields.firm_name, 1)
    filled = filled.replace("<country>", fields.country, 1)
    filled = filled.replace("<date>", fields.checked_on, 1)
    filled = filled.replace("<number>", fields.listing_status, 1)
    filled = filled.replace(
        "final URL after redirects: <url>",
        f"final URL after redirects: {fields.listing_final_url}",
        1,
    )
    filled = filled.replace("<host>/robots.txt", host_line, 1)
    filled = filled.replace(
        "Job page domain: <url>",
        f"Job page domain: {fields.job_page_domain}",
        1,
    )
    filled = filled.replace(
        "Apply button goes to (domain): <url>",
        f"Apply button goes to (domain): {fields.apply_domains}",
        1,
    )
    filled = filled.replace(
        "Conclusion: confirmed / likely / unknown :<best guess>",
        f"Conclusion: {fields.conclusion} :",
        1,
    )
    filled = filled.replace(
        "reached from the firm's homepage):",
        f"reached from the firm's homepage): {fields.careers_entry_url}",
        1,
    )
    filled = filled.replace(
        "Job-search / listing page:",
        f"Job-search / listing page: {fields.listing_url}",
        1,
    )
    filled = filled.replace(
        "used for tests 4 and 5):",
        f"used for tests 4 and 5): {fields.example_job_url}",
        1,
    )
    filled = filled.replace(
        "Result: allowed / disallowed for the listing path / "
        "disallowed for job pages / no robots.txt (404)",
        f"Result: {fields.robots_result}",
        1,
    )
    filled = filled.replace(
        'Paste the "User-agent: *" block and any "Sitemap:" lines',
        'Paste the "User-agent: *" block and any "Sitemap:" lines\n'
        + _indent_block(fields.robots_paste),
        1,
    )
    filled = filled.replace(
        "JSON-LD JobPosting on the example job page: yes / no / no posting to test",
        f"JSON-LD JobPosting on the example job page: {fields.jsonld_status}",
        1,
    )
    filled = filled.replace(
        "If yes: paste the first ~30 lines of the JSON-LD block",
        "If yes: paste the first ~30 lines of the JSON-LD block\n"
        + _indent_block(fields.jsonld_excerpt),
        1,
    )
    filled = filled.replace(
        "RSS / sitemap: found / not found. If found, the URL and where you saw it",
        f"RSS / sitemap: {fields.rss_sitemap}",
        1,
    )
    filled = filled.replace(
        "Vendor names found in page source / network: workday, smartrecruiters, "
        "greenhouse, ever, taleo, oraclecloud, eightfold, phenom, teamtailor, "
        "workable, personio, recruitee, ashby.",
        f"Vendor names found in page source / network: {fields.vendors_found}",
        1,
    )
    filled = filled.replace(
        "Is a visible job title present in view-source (Ctrl+U)? yes / no",
        f"Is a visible job title present in view-source (Ctrl+U)? {fields.title_in_source}",
        1,
    )
    filled = filled.replace(
        "its status, and the first ~20 lines of the response",
        "its status, and the first ~20 lines of the response\n"
        + _indent_block(fields.xhr_note),
        1,
    )
    filled = filled.replace(
        '429, stop and write "blocked"',
        f'429, stop and write "blocked"\n     {fields.obstacles}',
        1,
    )
    filled = filled.replace(
        "9. Notes (optional): how many internship postings are visible, anything odd",
        f"9. Notes (optional): {fields.notes}",
        1,
    )
    filled = filled.replace(
        "Terms / legal-notice URL:",
        f"Terms / legal-notice URL: {fields.terms_url}",
        1,
    )
    filled = filled.replace(
        "Mentions automated access (robots, crawling, scraping, bots)? "
        "yes / no / terms page not found",
        f"Mentions automated access (robots, crawling, scraping, bots)? "
        f"{fields.terms_mentions}",
        1,
    )
    filled = filled.replace(
        "If yes: paste the sentence (1–2 sentences max) and its language",
        "If yes: paste the sentence (1–2 sentences max) and its language\n"
        + _indent_block(fields.terms_sentences),
        1,
    )

    leftover = ("<name>", "<country>", "<date>", "<number>", "<url>", "<host>", "<best guess>")
    if any(token in filled for token in leftover):
        raise ValueError("renderer left a template placeholder unfilled")

    return filled


def fields_from_csv_row(row: dict[str, str]) -> SurveyFields:
    """Start a survey from CSV only. Later steps add observed HTTP fields."""
    if not row["firm"]:
        raise ValueError("firm name is empty in survey_input.csv")
    return SurveyFields(
        firm_name=row["firm"],
        country=row.get("country", "") or MANUAL,
        careers_entry_url=row["careers_entry_url"],
        listing_url=row["listing_url"],
        example_job_url=row["example_job_url"],
        example_job_title=row["example_job_title"],
        terms_url=row["terms_url"],
    )


def write_survey_file(output_dir: Path, fields: SurveyFields) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{firm_slug(fields.firm_name)}.md"
    path.write_text(render_survey(fields), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Step 4: survey one firm, then the command line.
# Every value written to the survey is either something the script observed
# or a visible "failed: ..." / "blocked" / "disallowed" / MANUAL marker.
# ---------------------------------------------------------------------------

MISSING_CSV = "failed: missing in survey_input.csv"
INVALID_URL = "failed: invalid URL in survey_input.csv (must start with http:// or https://)"
NO_ROBOTS = "no robots.txt (404)"
LOGIN_COOKIE_NOTE = "not scripted: login wall and cookie wall (check in the browser)"


@dataclass
class Page:
    """One URL from the CSV and what happened when we fetched it."""

    label: str  # e.g. "listing page", used in the survey text
    url: str
    result: FetchResult | None  # None if the URL was empty or invalid
    error: str | None = None  # set when the CSV value cannot be fetched

    @property
    def given(self) -> bool:
        """True if the CSV has a value for this page (valid or not)."""
        return bool(self.url)

    @property
    def problem(self) -> str | None:
        """None if the page can be parsed, else the reason (never "no")."""
        if self.error:
            return self.error
        if self.result is None:
            return MISSING_CSV
        return page_problem(self.result)

    def problem_text(self) -> str:
        """The problem, plus the redirect target for a cross-host redirect."""
        reason = self.problem or "failed: unknown"
        if self.result is not None and self.result.redirect_target:
            reason += f" to {self.result.redirect_target}"
        return reason

    @property
    def text(self) -> str:
        """HTML text. Only call when problem is None."""
        assert self.result is not None and self.result.text is not None
        return self.result.text

    @property
    def final_url(self) -> str:
        assert self.result is not None
        return self.result.final_url


def is_fetchable_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def fetch_page(client: HttpClient, label: str, url: str) -> Page:
    if not url:
        return Page(label, url, None)
    if not is_fetchable_url(url):
        return Page(label, url, None, error=INVALID_URL)
    return Page(label, url, client.get(url))


def fetch_pages(client: HttpClient, row: dict[str, str]) -> dict[str, Page]:
    """Fetch the four pages. robots.txt, rate limit and blocks live in the client."""
    return {
        "careers": fetch_page(client, "careers entry page", row["careers_entry_url"]),
        "listing": fetch_page(client, "listing page", row["listing_url"]),
        "job": fetch_page(client, "example job page", row["example_job_url"]),
        "terms": fetch_page(client, "terms page", row["terms_url"]),
    }


# ---- Item 2 -----------------------------------------------------------------


def describe_status(result: FetchResult) -> str:
    if result.status is None:
        return result.outcome
    if result.outcome == "ok":
        return str(result.status)
    return f"{result.status} ({result.outcome})"


def listing_status_fields(listing: Page) -> tuple[str, str]:
    """(status, final URL) for item 2."""
    if listing.result is None:
        reason = listing.error or MISSING_CSV
        return reason, reason
    result = listing.result
    final = result.final_url
    if result.redirect_target:
        final += f" (redirects to {result.redirect_target}, not followed)"
    return describe_status(result), final


# ---- Item 3 -----------------------------------------------------------------


def robots_result_text(
    client: HttpClient, record: RobotsRecord, listing_url: str, job_url: str
) -> str:
    if record.outcome != "ok":
        return record.outcome  # "no robots.txt (404)", "blocked" or "failed: ..."
    problems = []
    if not client.allowed_by_robots(listing_url, record):
        problems.append("disallowed for the listing path")
    job_note = ""
    if not job_url:
        job_note = "listing path only; no example job URL"
    elif not is_fetchable_url(job_url):
        job_note = "listing path only; example job URL is invalid"
    elif origin_of(job_url) != record.origin:
        job_note = "listing path only; the example job page is on another host, see the host line"
    elif not client.allowed_by_robots(job_url, record):
        problems.append("disallowed for job pages")
    if problems:
        return " / ".join(problems)
    return f"allowed ({job_note})" if job_note else "allowed"


def robots_paste_text(record: RobotsRecord) -> str:
    if record.outcome == NO_ROBOTS:
        return "n/a (no robots.txt)"
    if record.outcome != "ok":
        return f"n/a ({record.outcome})"
    star = record.star_block or "(no 'User-agent: *' block in robots.txt)"
    sitemaps = "\n".join(record.sitemap_lines) or "(no Sitemap lines)"
    return star + "\n" + sitemaps


def other_hosts_note(client: HttpClient, pages: dict[str, Page], listing_origin: str) -> str:
    """Every other host we fetched from gets its own robots.txt check; say so."""
    notes: list[str] = []
    seen: set[str] = set()
    for page in pages.values():
        if not page.given or not is_fetchable_url(page.url):
            continue
        origin = origin_of(page.url)
        if origin == listing_origin or origin in seen:
            continue
        seen.add(origin)
        record = client.load_robots(page.url)
        label = "robots.txt read" if record.outcome == "ok" else record.outcome
        notes.append(f"{page.label} is on {origin}: {label}")
    return "; ".join(notes)


# ---- Item 4 -----------------------------------------------------------------


def jsonld_fields(job: Page) -> tuple[str, str]:
    """(status, excerpt) for item 4."""
    if not job.given:
        return "no posting to test", "n/a"
    if job.problem:
        return job.problem_text(), "n/a"
    status, excerpt = find_jobposting_jsonld(job.text)
    return status, excerpt or "n/a"


# ---- Item 5 -----------------------------------------------------------------


def rss_sitemap_text(pages: dict[str, Page], record: RobotsRecord | None) -> str:
    found: list[str] = []
    problems: list[str] = []
    if record is None:
        problems.append("robots.txt: no listing URL")
    elif record.outcome in ("ok", NO_ROBOTS):
        for line in record.sitemap_lines:
            found.append(f"{line} (seen in {record.robots_url}; contents not checked)")
    else:
        problems.append(f"robots.txt: {record.outcome}")
    for key in ("careers", "listing"):
        page = pages[key]
        if page.problem:
            problems.append(f"{page.label}: {page.problem_text()}")
            continue
        for feed in find_feed_links(page.text, page.final_url):
            found.append(f"{feed} (<link rel=alternate> on the {page.label})")
    if found:
        text = "found: " + "; ".join(found)
        if problems:
            text += " | not fully checked: " + "; ".join(problems)
        return text
    if problems:
        return "failed: could not check everything: " + "; ".join(problems)
    return (
        "not found (checked robots.txt Sitemap lines and <link rel=alternate> "
        "on the careers entry and listing pages)"
    )


# ---- Item 6 -----------------------------------------------------------------


def job_domain_fields(job: Page) -> tuple[str, str]:
    """(job page domain, apply-button text) for item 6."""
    if not job.given:
        return "n/a (no example job URL)", MANUAL
    if job.problem:
        return job.problem_text(), MANUAL
    domain = hostname_of(job.final_url)
    outbound = ", ".join(outbound_link_hosts(job.text, job.final_url)) or "none"
    return domain, (
        f"{MANUAL} (the script does not guess the apply button; "
        f"outbound link domains on the job page: {outbound})"
    )


def vendor_fields(pages: dict[str, Page]) -> tuple[str, str]:
    """(vendors text, conclusion) for item 6. Conclusion is only likely/unknown."""
    hits: dict[str, list[str]] = {}
    checked: list[str] = []
    unchecked: list[str] = []
    for key in ("careers", "listing", "job"):
        page = pages[key]
        if page.problem:
            unchecked.append(f"{page.label} ({page.problem_text()})")
            continue
        checked.append(page.label)
        hosts = all_referenced_hosts(page.text, page.final_url)
        for vendor, vendor_hosts in find_vendor_hits(hosts).items():
            entries = hits.setdefault(vendor, [])
            for host in vendor_hosts:
                if not any(entry.startswith(host + " ") for entry in entries):
                    entries.append(f"{host} (on the {page.label})")
    if hits:
        text = "; ".join(f"{vendor}: {', '.join(e)}" for vendor, e in hits.items())
    elif checked:
        text = (
            "none found in link, script and form hostnames of: "
            + ", ".join(checked)
            + " (vendors loaded only by JavaScript would be missed)"
        )
    else:
        text = "failed: no page could be parsed"
    if unchecked:
        text += " | not checked: " + "; ".join(unchecked)
    return text, vendor_conclusion(hits)


# ---- Item 7a ----------------------------------------------------------------


def title_in_source_text(listing: Page, title: str) -> str:
    if listing.problem:
        return listing.problem_text()
    found = title_in_html(title, listing.text)
    if found is None:
        return "failed: example_job_title missing in survey_input.csv"
    if found:
        return "yes"
    return "no (script checked the raw HTML only; confirm with Ctrl+U and fill the line below)"


# ---- Item 8 -----------------------------------------------------------------


def obstacles_text(pages: dict[str, Page]) -> str:
    lines: list[str] = []
    for page in pages.values():
        result = page.result
        if result is not None and result.outcome == "blocked":
            status = result.status if result.status is not None else "no status"
            lines.append(f"blocked: {page.label} {page.url} (HTTP {status})")
    for page in pages.values():
        if page.problem is None:
            for hint in find_obstacle_hints(page.text):
                lines.append(f"hint on the {page.label}: {hint}")
    lines.append(LOGIN_COOKIE_NOTE)
    return "\n     ".join(lines)


# ---- Item 10 ----------------------------------------------------------------


def terms_fields(terms: Page) -> tuple[str, str]:
    """(mentions, sentences) for item 10."""
    if not terms.given:
        return "terms page not found", "n/a"
    if terms.problem:
        return terms.problem_text(), "n/a"
    result = assess_terms(terms.text)
    if result.status != "yes":
        return result.status, "n/a"
    lines = [f"[{language}, guessed] {sentence}" for sentence, language in result.sentences]
    if result.total_matches > len(result.sentences):
        lines.append(
            f"({result.total_matches} matching sentences; first {len(result.sentences)} shown)"
        )
    return "yes", "\n".join(lines)


# ---- One firm ---------------------------------------------------------------


def survey_firm(client: HttpClient, row: dict[str, str], checked_on: str) -> SurveyFields:
    """Fetch the firm's pages and fill every field the script can observe.

    Items 7b (XHR call) and 9 (notes) stay MANUAL. So does the apply button.
    """
    fields = fields_from_csv_row(row)
    fields.checked_on = checked_on
    pages = fetch_pages(client, row)
    listing = pages["listing"]

    fields.listing_status, fields.listing_final_url = listing_status_fields(listing)

    record: RobotsRecord | None = None
    if listing.given and is_fetchable_url(listing.url):
        record = client.load_robots(listing.url)
        fields.robots_host = origin_of(listing.url)
        fields.robots_result = robots_result_text(
            client, record, listing.url, row["example_job_url"]
        )
        fields.robots_paste = robots_paste_text(record)
        fields.extra_hosts_note = other_hosts_note(client, pages, record.origin)
    else:
        reason = listing.error or MISSING_CSV
        fields.robots_host = f"n/a ({reason})"
        fields.robots_result = reason
        fields.robots_paste = "n/a"
        fields.extra_hosts_note = other_hosts_note(client, pages, "")

    fields.jsonld_status, fields.jsonld_excerpt = jsonld_fields(pages["job"])
    fields.rss_sitemap = rss_sitemap_text(pages, record)
    fields.job_page_domain, fields.apply_domains = job_domain_fields(pages["job"])
    fields.vendors_found, fields.conclusion = vendor_fields(pages)
    fields.title_in_source = title_in_source_text(listing, row["example_job_title"])
    fields.obstacles = obstacles_text(pages)
    fields.terms_mentions, fields.terms_sentences = terms_fields(pages["terms"])
    return fields


def run_survey(
    rows: list[dict[str, str]],
    client: HttpClient,
    output_dir: Path,
    checked_on: str,
) -> list[str]:
    """Survey each firm. One firm failing never stops the others, and is reported."""
    errors: list[str] = []
    for row in rows:
        name = row.get("firm") or "(no firm name)"
        try:
            path = write_survey_file(output_dir, survey_firm(client, row, checked_on))
            print(f"{name}: written to {path}")
        except Exception as exc:  # reported below, never swallowed
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"{name}: ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
    return errors


def write_run_log(output_dir: Path, user_agent: str, checked_on: str, requests: list[str]) -> Path:
    """List every URL requested, so the politeness of the run can be audited."""
    path = output_dir / "_run_log.txt"
    header = [
        f"Source survey run on {checked_on}",
        f"User-Agent: {user_agent}",
        f"{len(requests)} requests, in order:",
    ]
    path.write_text("\n".join(header + requests) + "\n", encoding="utf-8")
    return path


CONTACT_PATTERN = re.compile(r"[^\s()<>;,]+@[^\s()<>;,]+")


def build_user_agent(contact: str) -> str:
    """Put the contact address into USER_AGENT. Empty keeps the placeholder."""
    if not contact:
        return USER_AGENT
    if not CONTACT_PATTERN.fullmatch(contact):
        raise SystemExit(f"--contact must look like an email address, got: {contact!r}")
    return USER_AGENT.replace(CONTACT_PLACEHOLDER, contact)


def select_rows(rows: list[dict[str, str]], only: str) -> list[dict[str, str]]:
    if not only:
        return rows
    wanted = {firm_slug(name) for name in only.split(",") if name.strip()}
    chosen = [row for row in rows if firm_slug(row["firm"]) in wanted]
    missing = wanted - {firm_slug(row["firm"]) for row in chosen}
    if missing:
        raise SystemExit("--only: no such firm in the CSV: " + ", ".join(sorted(missing)))
    return chosen


def main(
    argv: list[str] | None = None,
    transport: Transport | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:
    """Without --live nothing is fetched. With --live, every firm is surveyed."""
    parser = argparse.ArgumentParser(description="Step 0d source survey.")
    parser.add_argument("--live", action="store_true", help="make real HTTP requests")
    parser.add_argument(
        "--contact",
        default=os.environ.get("SURVEY_CONTACT", ""),
        help="your email, put in the User-Agent (or set SURVEY_CONTACT)",
    )
    parser.add_argument("--input", type=Path, default=None, help="default: survey_input.csv")
    parser.add_argument("--output", type=Path, default=None, help="default: survey_output")
    parser.add_argument("--only", default="", help="comma-separated firm names to run")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    user_agent = build_user_agent(args.contact)
    if args.live:
        require_live_user_agent(user_agent)

    csv_path = args.input or REPO_ROOT / "survey_input.csv"
    default_dir = "survey_output" if args.live else "survey_dryrun"
    output_dir = args.output or REPO_ROOT / default_dir
    rows = select_rows(load_survey_rows(csv_path), args.only)
    checked_on = datetime.date.today().isoformat()

    if not args.live:
        # Dry run: check the CSV and template, contact no website. Files go to
        # survey_dryrun so they can never overwrite real results.
        print("DRY RUN: no website is contacted. Files hold MANUAL placeholders only.")
        errors = []
        for row in rows:
            try:
                path = write_survey_file(output_dir, fields_from_csv_row(row))
                print(f"{row['firm']}: template check written to {path}")
            except Exception as exc:
                errors.append(f"{row['firm'] or '(no firm name)'}: {exc}")
                print(f"ERROR {errors[-1]}", file=sys.stderr)
        return 1 if errors else 0

    client = HttpClient(user_agent, transport=transport, sleeper=sleeper, monotonic=monotonic)
    errors = run_survey(rows, client, output_dir, checked_on)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = write_run_log(output_dir, user_agent, checked_on, client.request_log)
    print(f"{len(client.request_log)} requests made; log: {log_path}")
    if errors:
        print(f"{len(errors)} firm(s) failed:", file=sys.stderr)
        for message in errors:
            print(f"  {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
