"""Tests for structured.py and the extended html_scan.py (step T3.2). Offline, firm-neutral.

All JSON-LD blocks and feeds are SYNTHETIC, written from the schema.org, RSS 2.0,
RSS 1.0 and Atom standards. No real feed or job page has been captured yet.
"""

import json
import random

import pytest

from tracker.triage.contract import EVIDENCE_KINDS, MAX_DETAIL
from tracker.triage.html_scan import scan_html
from tracker.triage.structured import (
    analyze_feed,
    analyze_jobposting,
    find_feed_links,
)

PAGE = "https://www.example.test/careers/job/1"
FEED = "https://jobs.example.test/feeds/jobs.xml"
EXAMPLE = "https://jobs.example.test/jobs/view/100"


def ld(*blocks: str) -> str:
    return "<html><head>" + "".join(
        f'<script type="application/ld+json">{block}</script>' for block in blocks) + "</head></html>"


# -- scanner additions ----------------------------------------------------------------------------

def test_the_scanner_collects_resources_jsonld_and_feed_links():
    page = scan_html(
        '<link rel="stylesheet" href="/a.css"><script src="/a.js"></script><form action="/go"></form>'
        '<link rel="alternate" type="application/rss+xml; charset=utf-8" href="/feed.xml">'
        '<link rel="alternate" hreflang="fr" href="/fr/">'
        '<script type="Application/LD+JSON; charset=utf-8">{"a": 1}</script><script>var x = 1;</script>')
    assert page.resource_urls == (("link", "/a.css"), ("script", "/a.js"), ("form", "/go"),
                                  ("link", "/feed.xml"), ("link", "/fr/"))
    assert page.jsonld_blocks == ('{"a": 1}',)
    assert page.feed_links == (("/feed.xml", "application/rss+xml"),)


def test_an_unclosed_jsonld_script_is_still_collected():
    assert scan_html('<script type="application/ld+json">{"a": 1}').jsonld_blocks == ('{"a": 1}',)


def test_a_page_cut_inside_a_jsonld_script_gives_the_text_so_far_and_an_unreadable_note():
    """Pages are cut at the size cap. The analyzer must see the partial block and say it is unreadable."""
    cut = '<html><body><script type="application/ld+json">{"@type": "JobPosting", "title": "Ana'
    assert scan_html(cut).jsonld_blocks == ('{"@type": "JobPosting", "title": "Ana',)
    (evidence,) = analyze_jobposting(PAGE, cut)
    assert evidence.kind == "note" and "could not be read" in evidence.detail


def test_closing_the_script_ourselves_changes_nothing_for_well_formed_pages():
    page = scan_html('<script type="application/ld+json">{"a": 1}</script><script type="application/ld+json">[2]</script>'
                     '<script>var x = 1;</script><p>end</p>')
    assert page.jsonld_blocks == ('{"a": 1}', "[2]")


def test_an_unclosed_ordinary_script_at_the_end_is_harmless():
    page = scan_html('<a href="/j/1">x</a><script>var a = "<";')
    assert page.hrefs == ("/j/1",) and page.jsonld_blocks == ()


def test_a_link_with_rel_alternate_among_other_relations_is_a_feed_link():
    page = scan_html('<link rel="alternate home" type="application/atom+xml" href="/atom">')
    assert page.feed_links == (("/atom", "application/atom+xml"),)


def test_other_alternate_types_are_not_feed_links():
    assert scan_html('<link rel="alternate" type="text/html" href="/x"><link type="application/rss+xml" href="/y">').feed_links == ()


# -- JSON-LD JobPosting -----------------------------------------------------------------------------

POSTING = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Analyst Intern", "hiringOrganization": "X"}


def test_a_jobposting_block_is_found_with_its_title():
    (evidence,) = analyze_jobposting(PAGE, ld(json.dumps(POSTING)))
    assert evidence.kind == "jsonld_jobposting" and evidence.url == PAGE
    assert evidence.detail == "JobPosting, title: Analyst Intern"


def test_name_is_used_when_there_is_no_title():
    (evidence,) = analyze_jobposting(PAGE, ld(json.dumps({"@type": "JobPosting", "name": "Actuarial Intern"})))
    assert "Actuarial Intern" in evidence.detail


@pytest.mark.parametrize("data", [
    [POSTING],
    {"@graph": [{"@type": "Organization"}, POSTING]},
    [{"@graph": [POSTING]}],
    {**POSTING, "@type": ["Thing", "JobPosting"]},
    {**POSTING, "@type": "https://schema.org/JobPosting"},
])
def test_a_jobposting_is_found_in_lists_graphs_type_lists_and_full_iris(data):
    assert [e.kind for e in analyze_jobposting(PAGE, ld(json.dumps(data)))] == ["jsonld_jobposting"]


@pytest.mark.parametrize("data", [
    {"@type": "Organization", "name": "X"},
    {"@type": "Article"},
    {"@type": "JobPostingX"},
    {"title": "no type at all"},
    {"@type": ["WebSite"], "about": POSTING},      # only top-level, lists and @graph are searched
    "just a string", 42, None,
])
def test_other_structured_data_is_not_a_jobposting(data):
    assert analyze_jobposting(PAGE, ld(json.dumps(data))) == []


def test_several_postings_are_counted_in_one_finding():
    (evidence,) = analyze_jobposting(PAGE, ld(json.dumps([POSTING, POSTING, POSTING])))
    assert evidence.detail.endswith("(+2 more)")


def test_a_posting_without_title_says_so_and_a_long_title_is_cut():
    (a,) = analyze_jobposting(PAGE, ld(json.dumps({"@type": "JobPosting"})))
    assert a.detail == "JobPosting, no title"
    (b,) = analyze_jobposting(PAGE, ld(json.dumps({"@type": "JobPosting", "title": "T" * 500})))
    assert len(b.detail) <= MAX_DETAIL and "T" * 100 in b.detail and "T" * 101 not in b.detail


@pytest.mark.parametrize("block", ["{not json", '{"a": }', "", "<!-- comment -->", "[" * 5000 + "]" * 5000])
def test_a_block_that_cannot_be_read_is_a_visible_note_not_silence(block):
    (evidence,) = analyze_jobposting(PAGE, ld(block))
    assert evidence.kind == "note" and "could not be read" in evidence.detail


def test_a_good_block_next_to_a_bad_one_gives_both_findings():
    kinds = [e.kind for e in analyze_jobposting(PAGE, ld("{bad", json.dumps(POSTING)))]
    assert kinds == ["jsonld_jobposting", "note"]


def test_a_page_without_structured_data_gives_nothing():
    assert analyze_jobposting(PAGE, "<html><body>no data</body></html>") == []


# -- feed links ---------------------------------------------------------------------------------------

def test_feed_links_are_resolved_deduplicated_and_called_a_lead():
    page = scan_html('<link rel="alternate" type="application/rss+xml" href="/jobs.rss">'
                     '<link rel="alternate" type="application/rss+xml" href="https://www.example.test/jobs.rss">'
                     '<link rel="alternate" type="application/atom+xml" href="/jobs.atom">')
    found = find_feed_links(PAGE, page)
    assert [e.detail for e in found] == [
        "application/rss+xml: https://www.example.test/jobs.rss",
        "application/atom+xml: https://www.example.test/jobs.atom",
    ]
    assert all(e.kind == "feed_link" for e in found)


# -- feeds ----------------------------------------------------------------------------------------------

def rss(links, title="Jobs") -> str:
    items = "".join(f"<item><title>t{n}</title><link>{link}</link></item>" for n, link in enumerate(links))
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{title}</title>{items}</channel></rss>'


def job_links(n: int) -> list[str]:
    return [f"https://jobs.example.test/jobs/view/{200 + i}" for i in range(n)]


def test_an_rss_feed_of_jobs_is_feed_parsed():
    findings = analyze_feed(FEED, rss(job_links(6)), EXAMPLE)
    assert findings.format == "RSS" and findings.item_count == 6 and findings.job_link_count == 6
    (evidence,) = findings.evidence
    assert evidence.kind == "feed_parsed" and evidence.url == FEED
    assert "jobs.example.test/jobs/view/*" in evidence.detail and "6 item(s)" in evidence.detail


def test_an_atom_feed_uses_the_alternate_link_not_self_or_enclosure():
    entries = "".join(
        f'<entry><title>t</title><link rel="self" href="https://jobs.example.test/api/{n}"/>'
        f'<link rel="enclosure" href="https://jobs.example.test/file/{n}"/>'
        f'<link href="https://jobs.example.test/jobs/view/{300 + n}"/></entry>' for n in range(5))
    text = f'<feed xmlns="http://www.w3.org/2005/Atom"><title>x</title>{entries}</feed>'
    findings = analyze_feed(FEED, text, EXAMPLE)
    assert (findings.format, findings.job_link_count) == ("Atom", 5)
    assert findings.evidence[0].kind == "feed_parsed"


def test_a_prefixed_atom_namespace_and_an_rdf_feed_are_read_too():
    atom = ('<a:feed xmlns:a="http://www.w3.org/2005/Atom">' + "".join(
        f'<a:entry><a:link href="https://jobs.example.test/jobs/view/{n}"/></a:entry>' for n in range(5)) + "</a:feed>")
    assert analyze_feed(FEED, atom, EXAMPLE).format == "Atom"
    rdf = ('<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/">'
           + "".join(f"<item><link>https://jobs.example.test/jobs/view/{n}</link></item>" for n in range(5))
           + "</rdf:RDF>")
    findings = analyze_feed(FEED, rdf, EXAMPLE)
    assert (findings.format, findings.job_link_count) == ("RDF", 5)


def test_relative_item_links_are_resolved_against_the_feed_address():
    findings = analyze_feed(FEED, rss([f"/jobs/view/{n}" for n in range(5)]), EXAMPLE)
    assert findings.job_link_count == 5


def test_a_blog_feed_is_not_a_job_feed():
    """One real firm's feed link pointed to a blog: the items decide, not the link."""
    findings = analyze_feed(FEED, rss([f"https://jobs.example.test/blog/2026/post-{n}" for n in range(12)]), EXAMPLE)
    assert findings.job_link_count == 0 and findings.item_count == 12
    (evidence,) = findings.evidence
    assert evidence.kind == "note" and "not a job feed" in evidence.detail


def test_four_matching_items_are_not_enough():
    findings = analyze_feed(FEED, rss(job_links(4)), EXAMPLE)
    assert findings.evidence[0].kind == "note" and "only 4" in findings.evidence[0].detail


def test_without_an_example_job_url_items_are_counted_but_not_judged():
    for example in (None, "", "  "):
        findings = analyze_feed(FEED, rss(job_links(9)), example)
        assert findings.evidence[0].kind == "note" and findings.item_count == 9
        assert "no example job URL" in findings.evidence[0].detail


def test_items_without_a_link_are_counted_but_give_no_links():
    text = '<rss><channel><item><title>a</title></item><item><link>  </link></item></channel></rss>'
    findings = analyze_feed(FEED, text, EXAMPLE)
    assert findings.item_count == 2 and findings.job_link_count == 0


def test_an_empty_feed_is_a_note_not_a_job_feed():
    findings = analyze_feed(FEED, '<rss version="2.0"><channel><title>x</title></channel></rss>', EXAMPLE)
    assert findings.evidence[0].kind == "note" and findings.item_count == 0


def test_a_declared_non_utf8_encoding_does_not_break_parsing():
    text = rss(["https://jobs.example.test/jobs/view/1"], title="é").replace("UTF-8", "ISO-8859-1")
    assert analyze_feed(FEED, text, EXAMPLE).format == "RSS"


@pytest.mark.parametrize("text, expect", [
    ('<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "x">]><rss><channel/></rss>', "DOCTYPE or ENTITY"),
    ('<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]><lolz>&lol2;</lolz>', "DOCTYPE or ENTITY"),
    ("<rss><channel>", "not well-formed"),
    ("not xml at all", "not well-formed"),
    ("", "not well-formed"),
    ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>x</loc></url></urlset>', "root element: urlset"),
    ("<html><body>hi</body></html>", "root element: html"),
])
def test_documents_that_are_not_usable_feeds_give_a_clear_note(text, expect):
    findings = analyze_feed(FEED, text, EXAMPLE)
    (evidence,) = findings.evidence
    assert evidence.kind == "note" and expect in evidence.detail
    assert findings.format is None and findings.job_link_count == 0


def test_random_text_never_raises_and_evidence_follows_the_contract():
    rng = random.Random(5)
    pieces = ["<rss>", "</rss>", "<channel>", "<item>", "</item>", "<link>", "</link>", "<feed>", "<entry>",
              "<link href=", '"/jobs/view/1"', "/>", "&", "<", ">", "é", " ", "<!DOCTYPE", "x"]
    for _ in range(400):
        text = "".join(rng.choice(pieces) for _ in range(rng.randrange(0, 30)))
        findings = analyze_feed(FEED, text, EXAMPLE)
        for item in findings.evidence:
            assert item.kind in EVIDENCE_KINDS and len(item.detail) <= MAX_DETAIL


# -- broken addresses must never crash an analyzer ----------------------------------------------------------

def test_a_broken_feed_link_is_skipped_not_fatal():
    page = scan_html('<link rel="alternate" type="application/rss+xml" href="http://[bad">'
                     '<link rel="alternate" type="application/rss+xml" href="/ok.rss">')
    assert [e.detail for e in find_feed_links(PAGE, page)] == ["application/rss+xml: https://www.example.test/ok.rss"]


def test_a_broken_item_link_in_a_feed_is_skipped_not_fatal():
    links = ["http://[bad"] + job_links(5)
    assert analyze_feed(FEED, rss(links), EXAMPLE).job_link_count == 5
