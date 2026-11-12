"""Decide what a listing page (or a sitemap) says about job links (step T3.1).

The problem: a careers page is full of links, and most are menus. On a real
listing page, links containing words like "offre" or "emploi" numbered 234, but
only 32 were jobs. So we do NOT guess from words. We use the example job URL the
user gave for the firm, and look for links with the same SHAPE.

The shape rule (spec, decided 11 Nov 2026 on three real captures: two listing
pages and one sitemap):

    1. Take the path segments of the example job URL; call their number N.
    2. The "fixed" part is the first N-1 segments (the last one is the job's own
       name or number). If the example carries its id in the query string
       (?id=123), the last segment is fixed too, so it is the first N segments,
       and candidate links must have the same query parameter names.
    3. Try prefixes from the longest fixed part down to the first two segments
       (or fewer if the fixed part is shorter).
    4. For each prefix, count the distinct links with the same host, the same
       prefix and the same number of segments N. The first prefix with at
       least 5 links wins: that is job_links evidence.
    5. Otherwise there is no evidence, only a note saying why.

A firm without an example job URL gets no job_links evidence, only a note.

Pure Python standard library. No network access in this file.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from tracker.triage.contract import Evidence
from tracker.triage.html_scan import scan_html
from tracker.triage.urls import resolve_url

MIN_JOB_LINKS = 5     # "several": at least this many links share the shape


@dataclass(frozen=True)
class JobLinkResult:
    count: int                  # links matching the winning shape (0 if none won)
    shape: str | None           # the winning shape in readable form, for example host/a/b/*
    note: str | None            # why there is no evidence, when count is 0


@dataclass(frozen=True)
class ListingFindings:
    evidence: tuple[Evidence, ...]
    job_link_count: int


def _parts(url: str):
    """(host, path segments, query parameter names), or None if not a usable http(s) URL."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host:
        return None
    segments = tuple(piece for piece in parts.path.split("/") if piece)
    keys = frozenset(urllib.parse.parse_qs(parts.query, keep_blank_values=True))
    return host, segments, keys


def count_job_links(hrefs: list[str] | tuple[str, ...], page_url: str, example_url: str) -> JobLinkResult:
    """Apply the shape rule. `hrefs` may be relative; they are resolved against `page_url`."""
    example = _parts(example_url)
    if example is None:
        return JobLinkResult(0, None, "the example job URL is not a usable http(s) address")
    host, segments, keys = example
    depth = len(segments)
    id_in_query = bool(keys)
    fixed = depth if id_in_query else depth - 1
    if fixed < 1:
        return JobLinkResult(0, None, "the example job URL has no path segment to build a shape from")

    candidates = set()
    for href in hrefs:
        absolute = resolve_url(page_url, href, drop_fragment=True)
        parts = _parts(absolute) if absolute else None
        if parts is not None:
            candidates.add((absolute, parts))

    floor = min(2, fixed)
    broadest = 0
    for length in range(fixed, floor - 1, -1):
        matches = {
            url for url, (link_host, link_segments, link_keys) in candidates
            if link_host == host
            and len(link_segments) == depth
            and link_segments[:length] == segments[:length]
            and (not id_in_query or link_keys == keys)
        }
        broadest = len(matches)
        if broadest >= MIN_JOB_LINKS:
            return JobLinkResult(broadest, _shape(host, segments, length, keys), None)
    return JobLinkResult(
        0, None,
        f"only {broadest} link(s) share the shape of the example job URL (at least {MIN_JOB_LINKS} needed)",
    )


def _shape(host: str, segments: tuple[str, ...], length: int, keys: frozenset[str]) -> str:
    text = host + "/" + "/".join(segments[:length]) + "/*" * (len(segments) - length)
    if keys:
        text += "?" + "&".join(f"{key}=*" for key in sorted(keys))
    return text


def analyze_listing(page_url: str, page_text: str, example_job_url: str | None = None) -> ListingFindings:
    """Evidence about job links and the app-shell marker for one listing page."""
    scan = scan_html(page_text)
    evidence: list[Evidence] = []
    count = 0

    if not example_job_url or not example_job_url.strip():
        evidence.append(Evidence.of("note", page_url, "no example job URL: job links were not counted"))
    else:
        result = count_job_links(scan.hrefs, page_url, example_job_url.strip())
        if result.count > 0:
            count = result.count
            evidence.append(Evidence.of(
                "job_links", page_url,
                f"{result.count} distinct links match {result.shape} (shape of the example job URL)"))
        else:
            evidence.append(Evidence.of("note", page_url, f"job links not found: {result.note}"))

    if count == 0 and scan.empty_root:
        evidence.append(Evidence.of(
            "spa_shell", page_url,
            f'empty <div id="root"> in the raw HTML (app shell); {len(scan.hrefs)} link(s) in the raw HTML'))
    return ListingFindings(tuple(evidence), count)
