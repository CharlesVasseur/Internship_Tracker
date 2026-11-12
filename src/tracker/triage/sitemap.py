"""Read a fetched sitemap and say whether it lists job URLs (step T3.4).

A sitemap is an XML file that lists a site's URLs. Two kinds exist:

    urlset         the list itself:   <urlset><url><loc>https://...</loc></url>...
    sitemapindex   a list of other sitemaps:   <sitemapindex><sitemap><loc>...

We read the <loc> entries with a plain text search, NOT with an XML parser, for
two reasons. A file cut at the size cap is no longer well-formed, and a strict
parser would reject all of it, while the text search still reads every complete
entry before the cut. And the standard XML parser can be abused by crafted files.
Only plain <loc> elements count: <image:loc>, <xhtml:link href=...> and similar
neighbours are ignored.

A urlset gives `sitemap_jobs` evidence when at least 5 distinct URLs share the
shape of the firm's example job URL (the same rule as listing pages, see
listing.count_job_links). Every other outcome is a note that says why: no example
job URL, too few matching URLs, an empty sitemap, an index, or a document that is
not a sitemap. A sitemap index is only reported: the caller decides which child
to fetch (the spec says: the first one).

Known limits: compressed sitemaps (.xml.gz) are not read here, <loc> with a
namespace prefix is not read, and the index branch is built from the sitemaps.org
standard and tested on synthetic files only (the real captures so far are one
urlset with URLs and one empty urlset).

Pure Python standard library. No network access in this file.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from tracker.triage.contract import Evidence
from tracker.triage.listing import MIN_JOB_LINKS, count_job_links
from tracker.triage.urls import resolve_url

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_ROOT = re.compile(r"<(urlset|sitemapindex)\b", re.I)
_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S)
_CDATA = re.compile(r"^<!\[CDATA\[(.*?)\]\]>$", re.S)


@dataclass(frozen=True)
class SitemapFindings:
    kind: str | None                      # "urlset", "sitemapindex", or None if not a sitemap
    urls: tuple[str, ...]                 # the <loc> entries of a urlset
    child_sitemaps: tuple[str, ...]       # the <loc> entries of a sitemap index
    job_url_count: int                    # URLs matching the job shape (0 if not counted)
    evidence: tuple[Evidence, ...]


def _locs(text: str) -> list[str]:
    found = []
    for raw in _LOC.findall(text):
        cdata = _CDATA.match(raw.strip())
        value = cdata.group(1).strip() if cdata else html.unescape(raw.strip())
        if value:
            found.append(value)
    return found


def read_sitemap(
    sitemap_url: str, text: str, example_job_url: str | None = None
) -> SitemapFindings:
    """Read the text of a fetched sitemap. Never raises."""
    cleaned = _COMMENT.sub("", text)
    root = _ROOT.search(cleaned)

    def note(message: str, kind=None, urls=(), children=()) -> SitemapFindings:
        return SitemapFindings(kind, tuple(urls), tuple(children), 0,
                               (Evidence.of("note", sitemap_url, message),))

    if root is None:
        return note("the document is not a sitemap (no <urlset> or <sitemapindex> element)")

    entries = _locs(cleaned[root.start():])
    if root.group(1).lower() == "sitemapindex":
        children = tuple(url for entry in entries if (url := resolve_url(sitemap_url, entry)))
        first = f"; the first is {children[0]}" if children else ""
        return note(f"sitemap index listing {len(children)} sitemap(s){first}", "sitemapindex", (), children)

    urls = tuple(entries)
    if not urls:
        return note("valid sitemap that lists no URLs", "urlset")
    if not example_job_url or not example_job_url.strip():
        return note(f"sitemap with {len(urls)} URL(s); no example job URL, so job URLs were not counted",
                    "urlset", urls)

    result = count_job_links(urls, sitemap_url, example_job_url.strip())
    if result.count >= MIN_JOB_LINKS:
        evidence = Evidence.of(
            "sitemap_jobs", sitemap_url,
            f"{result.count} of {len(urls)} listed URLs match {result.shape} (shape of the example job URL)")
        return SitemapFindings("urlset", urls, (), result.count, (evidence,))
    reason = result.note or "no listed URL matched"
    return note(f"sitemap with {len(urls)} URL(s) but no job URLs found: {reason}", "urlset", urls)
