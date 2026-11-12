"""Structured data on a page and feeds (step T3.2).

Two separate jobs, both pure text-in, evidence-out:

1. JSON-LD JobPosting. Job pages often carry a block of structured data that
   says "this is a job" in the schema.org vocabulary. We look for objects of type
   JobPosting, also inside "@graph" and lists. A block that cannot be read is
   reported as a note, never silently dropped.

2. Feeds (RSS 2.0, RSS 1.0 / RDF, Atom). A feed LINK on a page is only a lead:
   one real firm's link pointed to a blog, not to jobs. So a feed counts only after
   it has been fetched, parsed, and its items link to job-like URLs, using the
   same shape rule as listing pages (listing.count_job_links) and the firm's
   example job URL. Without that example, or with fewer than 5 matching items,
   the result is a note, not feed_parsed evidence.

Feed parsing is built from the RSS and Atom standards and tested on synthetic
feeds: no real feed has been captured yet. The XML reader of the standard
library can be abused by crafted files (entity bombs), so any document with a
DOCTYPE or ENTITY declaration is refused with a note: real feeds do not need them.

Pure Python standard library. No network access in this file.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from tracker.triage.contract import Evidence
from tracker.triage.html_scan import PageScan, scan_html
from tracker.triage.listing import MIN_JOB_LINKS, count_job_links
from tracker.triage.urls import resolve_url

MAX_TITLE = 100


# ---------------------------------------------------------------------------
# JSON-LD JobPosting
# ---------------------------------------------------------------------------


def _is_job_posting(node: object) -> bool:
    if not isinstance(node, dict):
        return False
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(k, str) and (k == "JobPosting" or k.endswith("/JobPosting")) for k in kinds)


def _job_postings(data: object) -> list[dict]:
    """JobPosting objects at the top, in a list, or inside '@graph'."""
    if isinstance(data, list):
        return [item for entry in data for item in _job_postings(entry)]
    if isinstance(data, dict):
        found = [data] if _is_job_posting(data) else []
        if isinstance(data.get("@graph"), list):
            found += _job_postings(data["@graph"])
        return found
    return []


def find_jobposting(page_url: str, scan: PageScan) -> list[Evidence]:
    evidence: list[Evidence] = []
    postings: list[dict] = []
    unreadable = 0
    for block in scan.jsonld_blocks:
        try:
            postings += _job_postings(json.loads(block))
        except (ValueError, RecursionError):
            unreadable += 1
    if postings:
        title = postings[0].get("name") or postings[0].get("title")
        label = f"title: {str(title)[:MAX_TITLE]}" if title else "no title"
        more = f" (+{len(postings) - 1} more)" if len(postings) > 1 else ""
        evidence.append(Evidence.of("jsonld_jobposting", page_url, f"JobPosting, {label}{more}"))
    if unreadable:
        evidence.append(Evidence.of(
            "note", page_url, f"{unreadable} JSON-LD block(s) could not be read as JSON"))
    return evidence


def analyze_jobposting(page_url: str, page_text: str) -> list[Evidence]:
    return find_jobposting(page_url, scan_html(page_text))


# ---------------------------------------------------------------------------
# Feed links on a page
# ---------------------------------------------------------------------------


def find_feed_links(page_url: str, scan: PageScan) -> list[Evidence]:
    """feed_link evidence: a lead to fetch, NOT proof of a job feed."""
    evidence = []
    seen = set()
    for href, kind in scan.feed_links:
        absolute = resolve_url(page_url, href)
        if absolute is not None and absolute not in seen:
            seen.add(absolute)
            evidence.append(Evidence.of("feed_link", page_url, f"{kind}: {absolute}"))
    return evidence


# ---------------------------------------------------------------------------
# Reading a fetched feed
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeedFindings:
    evidence: tuple[Evidence, ...]
    format: str | None       # "RSS", "RDF" or "Atom" when the document is a feed
    item_count: int          # items or entries found
    job_link_count: int      # item links that share the job shape (0 if not counted)


_UNSAFE_XML = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.I)


def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _item_links(root: ET.Element, feed_format: str) -> tuple[int, list[str]]:
    """(number of items, their links) for RSS/RDF items or Atom entries."""
    wanted = "entry" if feed_format == "Atom" else "item"
    count = 0
    links: list[str] = []
    for element in root.iter():
        if _local(element.tag) != wanted:
            continue
        count += 1
        for child in element:
            if _local(child.tag) != "link":
                continue
            if feed_format == "Atom":
                if child.get("rel", "alternate") == "alternate" and child.get("href"):
                    links.append(child.get("href").strip())
                    break
            elif child.text and child.text.strip():
                links.append(child.text.strip())
                break
    return count, links


def analyze_feed(feed_url: str, feed_text: str, example_job_url: str | None = None) -> FeedFindings:
    """Is this fetched document a feed whose items link to jobs?"""
    def note(message: str, fmt=None, items=0) -> FeedFindings:
        return FeedFindings((Evidence.of("note", feed_url, message),), fmt, items, 0)

    if _UNSAFE_XML.search(feed_text):
        return note("feed not parsed: it contains a DOCTYPE or ENTITY declaration (refused for safety)")
    try:
        root = ET.fromstring(feed_text)
    except ET.ParseError as error:
        return note(f"feed is not well-formed XML: {error}")

    root_name = _local(root.tag)
    feed_format = {"rss": "RSS", "RDF": "RDF", "feed": "Atom"}.get(root_name)
    if feed_format is None:
        return note(f"the XML document is not RSS, Atom or RDF (root element: {root_name or '?'})")

    count, links = _item_links(root, feed_format)
    if not example_job_url or not example_job_url.strip():
        return note(f"{feed_format} feed with {count} item(s); no example job URL, so items were not "
                    "checked for job links", feed_format, count)

    result = count_job_links(links, feed_url, example_job_url.strip())
    if result.count >= MIN_JOB_LINKS:
        evidence = Evidence.of(
            "feed_parsed", feed_url,
            f"{feed_format} feed, {count} item(s); {result.count} distinct item links match "
            f"{result.shape} (shape of the example job URL)")
        return FeedFindings((evidence,), feed_format, count, result.count)
    reason = result.note or "no item links matched"
    return note(f"{feed_format} feed with {count} item(s) but not a job feed: {reason}", feed_format, count)
