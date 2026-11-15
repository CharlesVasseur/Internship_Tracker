"""Triage one firm (step T4.2): fetch, analyze, choose the mode.

`triage_firm(firm, fetcher)` returns exactly one TriageResult and never raises.

What it does, in this order (the steps after the first are skipped, with a
visible note, once the request or time budget is used up):

    1. LinkedIn and Indeed are never fetched: ok / manual / policy evidence.
    2. Fetch the primary page: the listing URL if the firm has one, else the
       careers URL. This fetch decides the STATUS:
           blocked -> blocked      failed -> failed      ok -> carry on
    3. Listing page: job links, app-shell marker, recruiting-system hostnames,
       feed links.
    4. Example job page: JSON-LD JobPosting (not fetched again if it is the
       listing page itself).
    5. The first feed link, if any: fetched and read.
    6. The first Sitemap line of robots.txt, only if no job links and no job
       feed were found yet (a sitemap can only add a source of job URLs). If it
       is a sitemap index, the first child is read.
    7. The terms-of-use page: flagged and quoted, never judged.

A failure in steps 3 to 7 never changes the firm's status. It becomes a `note`
that says what could not be read and why. A failed or blocked check is never
an answer about the firm ("no openings", "not monitorable").

The MODE comes from the evidence, with the spec's priority:
    feed, json-ld, json-api, vendor, html, js-only, manual.
The verdict is derived by the contract. The terms flag is attached to the
result and is never read when the verdict is derived.

Budgets: at most 10 requests and 90 seconds per firm. Requests are counted
from the fetcher's own log (robots.txt and retries included). Both limits are
checked BETWEEN steps, so one step can overshoot: a fetch to a new site can
use robots.txt plus the page, each with up to 3 attempts, and a slow site can
take about 51 seconds. The primary page is always fetched.

Guard: any unexpected exception becomes failed/internal for that firm only,
keeping the evidence collected so far.

Pure Python standard library. This file never opens a connection itself: all
network access goes through the Fetcher it is given.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Callable, Iterable

from tracker.triage.contract import Evidence, FirmInput, TermsFlag, TriageError, TriageResult
from tracker.triage.fetch_result import FetchResult
from tracker.triage.fetcher import Fetcher
from tracker.triage.html_scan import PageScan, scan_html
from tracker.triage.listing import analyze_listing
from tracker.triage.sitemap import SitemapFindings, read_sitemap
from tracker.triage.structured import analyze_feed, analyze_jobposting, find_feed_links
from tracker.triage.terms import terms_from_fetch, visible_text
from tracker.triage.urls import resolve_url
from tracker.triage.vendors import analyze_vendors

MAX_REQUESTS_PER_FIRM = 10
MAX_SECONDS_PER_FIRM = 90.0
NEVER_FETCHED_LABELS = ("linkedin", "indeed")     # host labels: www.linkedin.com, fr.indeed.com

# Mode priority from the spec: the first kind of evidence present decides.
MODE_BY_EVIDENCE = (
    ("feed_parsed", "feed"),
    ("jsonld_jobposting", "json-ld"),
    ("api_json", "json-api"),
    ("vendor_host", "vendor"),
    ("job_links", "html"),
    ("spa_shell", "js-only"),
)


def choose_mode(evidence: Iterable[Evidence]) -> str:
    """The mode for an ok result: the first evidence kind in the spec's priority list."""
    kinds = {item.kind for item in evidence}
    for kind, mode in MODE_BY_EVIDENCE:
        if kind in kinds:
            return mode
    return "manual"


def triage_firm(
    firm: FirmInput,
    fetcher: Fetcher,
    *,
    clock: Callable[[], float] = time.monotonic,
    max_requests: int = MAX_REQUESTS_PER_FIRM,
    max_seconds: float = MAX_SECONDS_PER_FIRM,
) -> TriageResult:
    """Triage one firm. Never raises; always returns exactly one result."""
    work = _Work(firm, fetcher, clock, max_requests, max_seconds)
    try:
        return work.run()
    except Exception as error:      # a bug or a broken cache entry: report it, keep the run alive
        return work.internal_failure(error)


class _Work:
    """The state of one firm's triage: what was seen so far, and what was asked."""

    def __init__(
        self, firm: FirmInput, fetcher: Fetcher, clock: Callable[[], float],
        max_requests: int, max_seconds: float,
    ) -> None:
        self.firm = firm
        self.fetcher = fetcher
        self.clock = clock
        self.max_requests = max_requests
        self.max_seconds = max_seconds
        self.evidence: list[Evidence] = []
        self.terms: TermsFlag | None = None
        self._first_request = len(fetcher.request_log)
        self._started = clock()

    # -- small helpers ----------------------------------------------------------------

    def requests(self) -> tuple[str, ...]:
        return tuple(self.fetcher.request_log[self._first_request:])

    def note(self, url: str, message: str) -> None:
        self.evidence.append(Evidence.of("note", url, message))

    def budget_problem(self) -> str | None:
        used = len(self.fetcher.request_log) - self._first_request
        if used >= self.max_requests:
            return f"request budget used up ({used} of {self.max_requests})"
        elapsed = self.clock() - self._started
        if elapsed >= self.max_seconds:
            return f"time budget used up ({elapsed:.0f} s of {self.max_seconds:g} s)"
        return None

    def may_fetch(self, label: str, url: str) -> bool:
        problem = self.budget_problem()
        if problem is None:
            return True
        self.note(url, f"{label} not fetched: {problem}")
        return False

    def failure_note(self, label: str, result: FetchResult) -> None:
        """A secondary fetch that did not work: say so, never hide it."""
        if result.status == "blocked" and result.block is not None:
            why = f"blocked ({result.block.reason})"
        elif result.error is not None:
            why = f"failed ({result.error.kind}): {result.error.message}"
        else:
            why = "no usable answer"
        self.note(result.url, f"{label} not read: {why}")

    # -- the whole run ------------------------------------------------------------------

    def run(self) -> TriageResult:
        firm = self.firm
        if firm.api_url:
            self.note(firm.api_url, "api_url provided, not checked in this version")

        if self._never_fetched():
            return self._policy_result()

        primary = (firm.listing_url or "").strip() or firm.careers_url
        page = self.fetcher.fetch(primary)
        self._page_facts(page)
        if page.status == "ok":
            self._analyze_page(page)
        self._terms_step()
        return self._result(page)

    def internal_failure(self, error: Exception) -> TriageResult:
        message = f"{type(error).__name__}: {error}"
        return TriageResult.failed(
            self.firm, TriageError.of("internal", message, self.firm.careers_url or None),
            self.evidence, terms=self.terms, requests=self.requests())

    # -- step 1: policy ------------------------------------------------------------------

    def _never_fetched(self) -> bool:
        for url in (self.firm.careers_url, self.firm.listing_url):
            try:
                host = urllib.parse.urlsplit((url or "").strip()).hostname or ""
            except ValueError:
                continue
            if any(label in host.lower().split(".") for label in NEVER_FETCHED_LABELS):
                return True
        return False

    def _policy_result(self) -> TriageResult:
        url = (self.firm.listing_url or "").strip() or self.firm.careers_url
        self.evidence.append(Evidence.of(
            "policy", url, "LinkedIn and Indeed are never fetched (project policy): check by hand"))
        terms = TermsFlag(self.firm.terms_url, "not_assessed",
                          why="not fetched: LinkedIn and Indeed are never fetched; read the terms by hand")
        return TriageResult.ok(self.firm, "manual", self.evidence, terms=terms, requests=self.requests())

    # -- step 2: facts about the primary fetch ----------------------------------------------

    def _page_facts(self, page: FetchResult) -> None:
        rules = self.fetcher.robots_rules(page.final_url)
        if rules is not None:
            robots_url = resolve_url(page.final_url, "/robots.txt") or page.final_url
            self.evidence.append(Evidence.of("robots", robots_url, rules.describe()))
        if page.redirect_chain:
            self.evidence.append(Evidence.of(
                "redirect_chain", page.url, " -> ".join((page.url, *page.redirect_chain))))

    # -- steps 3 to 6: what the pages say ------------------------------------------------------

    def _analyze_page(self, page: FetchResult) -> None:
        text = page.text or ""
        base = page.final_url
        example = (self.firm.example_job_url or "").strip() or None
        scan = scan_html(text)

        self.evidence += analyze_listing(base, text, example).evidence
        extra = (("example job URL", example),) if example else ()
        self.evidence += analyze_vendors(base, text, extra)
        leads = find_feed_links(base, scan)
        self.evidence += leads
        if not scan.hrefs and not visible_text(text).strip():
            self.note(base, "the page has no visible text and no links in its raw HTML: nothing "
                            "machine-readable to read; a person should look at it")

        self._example_job_step(page, example)
        self._feed_step(base, scan, len(leads), example)
        self._sitemap_step(base, example)

    def _has(self, kind: str) -> bool:
        return any(item.kind == kind for item in self.evidence)

    def _example_job_step(self, page: FetchResult, example: str | None) -> None:
        if example is None:
            return
        if example in (page.url, page.final_url):           # the listing IS the example: read it once
            self.evidence += self._jobposting(page.final_url, page.text or "")
            return
        if not self.may_fetch("example job page", example):
            return
        job = self.fetcher.fetch(example)
        if job.ok:
            self.evidence += self._jobposting(job.final_url, job.text or "")
        else:
            self.failure_note("example job page", job)

    @staticmethod
    def _jobposting(url: str, text: str) -> list[Evidence]:
        found = analyze_jobposting(url, text)
        if any(item.kind == "jsonld_jobposting" for item in found):
            return found
        return found + [Evidence.of(
            "note", url,
            f"no JobPosting JSON-LD found in the raw HTML of the example job page ({len(text)} characters read)")]

    def _feed_step(self, base: str, scan: PageScan, lead_count: int, example: str | None) -> None:
        if not scan.feed_links:
            return
        feed_url = resolve_url(base, scan.feed_links[0][0])
        if feed_url is None:
            self.note(base, "the first feed link is not a usable address; not fetched")
            return
        if lead_count > 1:
            self.note(feed_url, f"{lead_count - 1} more feed link(s) on the page were not fetched")
        if not self.may_fetch("feed", feed_url):
            return
        feed = self.fetcher.fetch(feed_url)
        if feed.ok:
            self.evidence += analyze_feed(feed.final_url, feed.text or "", example).evidence
        else:
            self.failure_note("feed", feed)

    def _sitemap_step(self, base: str, example: str | None) -> None:
        robots_url = resolve_url(base, "/robots.txt") or base
        if self._has("job_links") or self._has("feed_parsed"):
            self.note(robots_url, "sitemap not fetched: job URLs were already found, so a sitemap adds nothing")
            return
        rules = self.fetcher.robots_rules(base)
        if rules is None:
            self.note(robots_url, "sitemap not checked: the robots.txt rules are not available")
            return
        if not rules.sitemaps:
            self.note(robots_url, "robots.txt has no Sitemap line")
            return
        first = rules.sitemaps[0]
        self.evidence.append(Evidence.of(
            "sitemap_line", robots_url, f"{len(rules.sitemaps)} Sitemap line(s); the first is {first}"))
        sitemap_url = resolve_url(robots_url, first)
        if sitemap_url is None:
            self.note(robots_url, f"the first Sitemap line is not a usable address: {first!r}")
            return
        findings = self._read_sitemap(sitemap_url, example)
        if findings is not None and findings.kind == "sitemapindex" and findings.child_sitemaps:
            self._read_sitemap(findings.child_sitemaps[0], example)

    def _read_sitemap(self, url: str, example: str | None) -> SitemapFindings | None:
        if not self.may_fetch("sitemap", url):
            return None
        result = self.fetcher.fetch(url, allow_truncated=True)
        if not result.ok:
            self.failure_note("sitemap", result)
            return None
        if result.truncated:
            self.note(result.final_url, "sitemap is larger than the size cap: read only up to its last complete entry")
        findings = read_sitemap(result.final_url, result.text or "", example)
        self.evidence += findings.evidence
        return findings

    # -- step 7: terms of use ---------------------------------------------------------------------

    def _terms_step(self) -> None:
        url = (self.firm.terms_url or "").strip()
        if not url:
            self.terms = TermsFlag(None, "not_assessed", why="no terms URL given; read the terms by hand")
            return
        problem = self.budget_problem()
        if problem is not None:
            self.terms = TermsFlag(url, "not_assessed", why=f"not fetched: {problem}; read the terms by hand")
            return
        self.terms = terms_from_fetch(self.fetcher.fetch(url))

    # -- the result ---------------------------------------------------------------------------

    def _result(self, page: FetchResult) -> TriageResult:
        firm, terms, requests = self.firm, self.terms, self.requests()
        if page.status == "blocked" and page.block is not None:
            return TriageResult.blocked(firm, page.block, self.evidence, terms=terms, requests=requests)
        if page.status == "failed" and page.error is not None:
            return TriageResult.failed(firm, page.error, self.evidence, terms=terms, requests=requests)
        mode = choose_mode(self.evidence)
        if mode == "manual":
            self.note(page.final_url, "no feed, JSON-LD JobPosting, known recruiting system or job links "
                                      "found in the raw HTML: a person checks this firm by hand")
        return TriageResult.ok(firm, mode, self.evidence, terms=terms, requests=requests)
