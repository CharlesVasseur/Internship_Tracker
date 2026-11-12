"""Tests for sitemap.py (step T3.4). Offline, firm-neutral.

Sitemaps here are SYNTHETIC, written from the sitemaps.org format. Checks on the
author's two real sitemaps live in test_triage_real_captures.py (skipped when the
local files are absent). The sitemap-index branch is tested on synthetic files only.
"""

import random
import time

import pytest

from tracker.triage.contract import EVIDENCE_KINDS, MAX_DETAIL
from tracker.triage.sitemap import read_sitemap

SITEMAP = "https://jobs.example.test/sitemap.xml"
EXAMPLE = "https://jobs.example.test/careers/job/PARIS/Analyst_JR100"
NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


def urlset(urls, extra_ns="", decl=True) -> str:
    entries = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    head = '<?xml version="1.0" encoding="UTF-8"?>\n' if decl else ""
    return f"{head}<urlset {NS}{extra_ns}>{entries}</urlset>"


def jobs(n, city="PARIS", start=0):
    return [f"https://jobs.example.test/careers/job/{city}/Title-{start + i}_JR{start + i}" for i in range(n)]


# -- urlset with jobs ---------------------------------------------------------------------------

def test_a_sitemap_of_jobs_gives_sitemap_jobs_evidence():
    found = read_sitemap(SITEMAP, urlset(jobs(8)), EXAMPLE)
    assert found.kind == "urlset" and len(found.urls) == 8 and found.job_url_count == 8
    (evidence,) = found.evidence
    assert evidence.kind == "sitemap_jobs" and evidence.url == SITEMAP
    assert evidence.detail == ("8 of 8 listed URLs match jobs.example.test/careers/job/PARIS/* "
                               "(shape of the example job URL)")


def test_jobs_mixed_with_other_pages_are_counted_among_the_total():
    pages = [f"https://jobs.example.test/about/{n}" for n in range(30)]
    found = read_sitemap(SITEMAP, urlset(pages + jobs(6)), EXAMPLE)
    assert found.job_url_count == 6 and "6 of 36 listed URLs" in found.evidence[0].detail


def test_the_city_folder_case_uses_the_shape_rule():
    cities = jobs(4, "DIJON") + jobs(3, "LYON", 10) + jobs(5, "NICE", 20)
    found = read_sitemap(SITEMAP, urlset(cities), "https://jobs.example.test/careers/job/DIJON/Title-1_JR1")
    assert found.job_url_count == 12 and found.evidence[0].kind == "sitemap_jobs"


def test_four_job_urls_are_not_enough_and_no_example_means_a_note():
    four = read_sitemap(SITEMAP, urlset(jobs(4)), EXAMPLE)
    assert four.evidence[0].kind == "note" and "no job URLs found" in four.evidence[0].detail
    for example in (None, "", "  "):
        none = read_sitemap(SITEMAP, urlset(jobs(9)), example)
        assert none.evidence[0].kind == "note" and "no example job URL" in none.evidence[0].detail
        assert len(none.urls) == 9 and none.job_url_count == 0


# -- reading <loc> entries -----------------------------------------------------------------------------

def test_entities_cdata_whitespace_and_extra_tags_are_handled():
    text = (f'<urlset {NS} xmlns:xhtml="http://www.w3.org/1999/xhtml" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">'
            "<url><loc>\n  https://jobs.example.test/a?x=1&amp;y=2 \n</loc><lastmod>2026-01-01</lastmod>"
            '<xhtml:link rel="alternate" hreflang="fr" href="https://jobs.example.test/fr/a"/>'
            "<image:image><image:loc>https://jobs.example.test/img.png</image:loc></image:image></url>"
            "<url><loc><![CDATA[https://jobs.example.test/b?p=1&q=2]]></loc></url></urlset>")
    assert read_sitemap(SITEMAP, text).urls == ("https://jobs.example.test/a?x=1&y=2",
                                               "https://jobs.example.test/b?p=1&q=2")


def test_a_missing_xml_declaration_and_comments_are_fine():
    text = "<!-- generated --><urlset><!-- <urlset> --><url><loc>https://jobs.example.test/x</loc></url></urlset>"
    assert read_sitemap(SITEMAP, text).urls == ("https://jobs.example.test/x",)


def test_empty_loc_elements_are_skipped():
    assert read_sitemap(SITEMAP, urlset(["", "  ", "https://jobs.example.test/ok"])).urls == ("https://jobs.example.test/ok",)


def test_a_file_cut_at_the_size_cap_still_gives_every_complete_entry():
    """The reason for a text search: a cut file is no longer well-formed XML."""
    full = urlset(jobs(10))
    cut = full[: full.index("Title-9_JR9") + 8]            # ends inside the last <loc>
    found = read_sitemap(SITEMAP, cut, EXAMPLE)
    assert len(found.urls) == 9 and found.job_url_count == 9


def test_duplicates_count_once_for_the_shape_rule():
    found = read_sitemap(SITEMAP, urlset(jobs(5) + jobs(5)), EXAMPLE)
    assert found.job_url_count == 5


def test_a_broken_loc_is_skipped_by_the_shape_rule_without_error():
    found = read_sitemap(SITEMAP, urlset(["http://[bad"] + jobs(6)), EXAMPLE)
    assert found.job_url_count == 6 and "http://[bad" in found.urls


# -- the other kinds of document ----------------------------------------------------------------------------------

def test_an_empty_urlset_is_reported_as_valid_and_empty():
    found = read_sitemap(SITEMAP, f'<?xml version="1.0" encoding="UTF-8"?><urlset {NS}></urlset>', EXAMPLE)
    assert found.kind == "urlset" and found.urls == () and found.job_url_count == 0
    assert found.evidence[0].kind == "note" and "no URLs" in found.evidence[0].detail


def test_a_sitemap_index_lists_its_children_and_does_not_count_jobs():
    text = (f'<sitemapindex {NS}><sitemap><loc>https://jobs.example.test/sitemap1.xml</loc></sitemap>'
            "<sitemap><loc>/sitemap2.xml</loc></sitemap></sitemapindex>")
    found = read_sitemap(SITEMAP, text, EXAMPLE)
    assert found.kind == "sitemapindex" and found.urls == ()
    assert found.child_sitemaps == ("https://jobs.example.test/sitemap1.xml", "https://jobs.example.test/sitemap2.xml")
    assert found.job_url_count == 0
    assert "2 sitemap(s); the first is https://jobs.example.test/sitemap1.xml" in found.evidence[0].detail


def test_an_empty_sitemap_index_is_handled():
    found = read_sitemap(SITEMAP, f"<sitemapindex {NS}></sitemapindex>")
    assert found.kind == "sitemapindex" and found.child_sitemaps == ()
    assert "0 sitemap(s)" in found.evidence[0].detail


@pytest.mark.parametrize("text, kind", [
    ("<html><body>hello</body></html>", None),
    ('<rss version="2.0"><channel><item><link>https://x.test/a</link></item></channel></rss>', None),
    ("", None),
    ("not xml", None),
])
def test_a_document_that_is_not_a_sitemap_says_so(text, kind):
    found = read_sitemap(SITEMAP, text, EXAMPLE)
    assert found.kind is kind and found.urls == ()
    assert found.evidence[0].kind == "note" and "not a sitemap" in found.evidence[0].detail


def test_a_loc_before_the_root_element_is_not_taken_for_a_url():
    text = "<loc>https://jobs.example.test/stray</loc>" + urlset(["https://jobs.example.test/real"])
    assert read_sitemap(SITEMAP, text).urls == ("https://jobs.example.test/real",)


# -- scale and safety ---------------------------------------------------------------------------------------------------

def test_a_big_sitemap_is_read_quickly():
    many = [f"https://jobs.example.test/careers/job/PARIS/T-{n}_JR{n}" for n in range(50000)]
    start = time.perf_counter()
    found = read_sitemap(SITEMAP, urlset(many), EXAMPLE)
    assert found.job_url_count == 50000 and time.perf_counter() - start < 5.0


def test_an_entity_bomb_is_just_text_and_does_nothing():
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
            f"<urlset {NS}><url><loc>&lol2;</loc></url></urlset>")
    found = read_sitemap(SITEMAP, bomb, EXAMPLE)
    assert found.urls == ("&lol2;",) and found.job_url_count == 0


def test_random_text_never_raises_and_evidence_follows_the_contract():
    rng = random.Random(3)
    pieces = ["<urlset>", "</urlset>", "<sitemapindex>", "<url>", "</url>", "<loc>", "</loc>", "<![CDATA[", "]]>",
              "<!--", "-->", "&amp;", "&", "<", ">", "https://jobs.example.test/job/1", "é", " ", "x"]
    for _ in range(400):
        text = "".join(rng.choice(pieces) for _ in range(rng.randrange(0, 40)))
        found = read_sitemap(SITEMAP, text, EXAMPLE)
        for item in found.evidence:
            assert item.kind in EVIDENCE_KINDS and len(item.detail) <= MAX_DETAIL
