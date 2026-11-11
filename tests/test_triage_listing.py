"""Tests for html_scan.py and listing.py (step T3.1). Offline, firm-neutral.

Every page and URL here is SYNTHETIC (hosts end in .test). They copy the SHAPES
seen on real pages (a menu full of look-alike links, jobs on another host, a
city folder in the middle of the path, an empty app shell). Checks on the
author's real captures live in test_triage_real_captures.py and are skipped
when those local files are absent.
"""

import random

import pytest

from tracker.triage.contract import EVIDENCE_KINDS, MAX_DETAIL
from tracker.triage.html_scan import PageScan, scan_html
from tracker.triage.listing import MIN_JOB_LINKS, analyze_listing, count_job_links


# -- scanning HTML ---------------------------------------------------------------------------------

def test_anchors_are_collected_as_written_and_entities_decoded():
    page = scan_html('<a href="/a?x=1&amp;y=2">one</a> <a href=" /b ">two</a> <a>none</a> <a href="">empty</a>')
    assert page.hrefs == ("/a?x=1&y=2", "/b")


@pytest.mark.parametrize("html", [
    '<div id="root"></div>',
    '<div id="root"/>',
    '<div id="root">  \n </div>',
    '<div id="root"><!-- filled later --></div>',
    '<DIV ID="ROOT"></DIV>',
    '<body><div id="root"></div></body>',
])
def test_an_empty_root_container_is_recognised(html):
    assert scan_html(html).empty_root is True


@pytest.mark.parametrize("html", [
    '<div id="root">Loading</div>',
    '<div id="root"><p></p></div>',
    '<div id="root"><div></div></div>',
    '<div id="root"><script></script></div>',
    '<div id="app"></div>',          # only "root" was seen on a real capture
    '<div class="root"></div>',
    "<p>no container at all</p>",
    "",
])
def test_anything_else_is_not_an_empty_root(html):
    assert scan_html(html).empty_root is False


def test_random_tag_soup_never_raises():
    rng = random.Random(11)
    pieces = ["<a href=", '"x"', "<div id=\"root\">", "</div>", "<", ">", "&amp;", "&", "<!--", "-->",
              "<script>", "</script>", "é", " ", "<a href='/j/1'>", "</a>", "\n", "<<", "=\""]
    for _ in range(400):
        text = "".join(rng.choice(pieces) for _ in range(rng.randrange(0, 40)))
        assert isinstance(scan_html(text), PageScan)


# -- look-alikes of the three page shapes seen on real captures ---------------------------------------------------

def anchors_to(urls) -> str:
    return "".join(f'<a href="{url}">x</a>' for url in urls)


def test_a_listing_with_32_offers_among_many_menu_links():
    menu = [f"/fr/brands/offers/{n}" for n in range(71)]          # look-alikes: contain the word "offers"
    offers = [f"https://jobs.example.test/fr/offers-list/job-{n}/" for n in range(32)]
    page = "<html><body>" + anchors_to(menu + offers) + "</body></html>"
    findings = analyze_listing("https://jobs.example.test/fr/brands/x/offers/", page,
                               "https://jobs.example.test/fr/offers-list/job-999/")
    assert findings.job_link_count == 32
    assert [e.kind for e in findings.evidence] == ["job_links"]      # content present: no app-shell flag
    assert "jobs.example.test/fr/offers-list/*" in findings.evidence[0].detail


def test_words_alone_would_have_counted_the_menu_too():
    """Why the rule uses the example's shape and not job words."""
    menu = [f"/fr/brands/offers/{n}" for n in range(71)]
    offers = [f"/fr/offers-list/job-{n}/" for n in range(32)]
    with_words = [h for h in scan_html(anchors_to(menu + offers)).hrefs if "offers" in h]
    assert len(with_words) == 103 and len(offers) == 32


def test_a_listing_whose_job_links_point_to_another_host():
    jobs = [f"https://careers.vendor.test/job/{1000 + n}" for n in range(72)]
    page = "<html><body>" + anchors_to(jobs + ["/about", "/contact"]) + "</body></html>"
    findings = analyze_listing("https://www.example.test/careers/open", page, "https://careers.vendor.test/job/1")
    assert findings.job_link_count == 72
    assert "careers.vendor.test/job/*" in findings.evidence[0].detail


APP_SHELL = (
    '<!DOCTYPE html><html><head><title></title>'
    '<script>window.app = {token: "placeholder", site: "x"};</script></head>'
    '<body><div id="root"></div></body></html>'
)


def test_an_empty_app_shell_has_no_job_links_and_gets_the_marker():
    findings = analyze_listing("https://jobs.example.test/", APP_SHELL, "https://jobs.example.test/job/CITY/title_1")
    assert findings.job_link_count == 0
    assert [e.kind for e in findings.evidence] == ["note", "spa_shell"]
    assert "0 link(s)" in findings.evidence[1].detail


# -- the same rule on a sitemap shaped like the real one (a city folder in the middle) ------------------------------

SITEMAP_URL = "https://jobs.example.test/careers/siteMap.xml"
CITIES = {"PARIS": 65, "LILLE": 6, "LYON": 5, "NANTES": 5, "DIJON": 4, "TOURS": 4,
          "RENNES": 3, "NICE": 3, "METZ": 3, "BREST": 2}


def city_sitemap() -> list[str]:
    urls = []
    for city, count in CITIES.items():
        urls += [f"https://jobs.example.test/careers/job/{city}/Title-{city}-{n}_JR{len(urls) + n}" for n in range(count)]
    return urls


def test_the_synthetic_sitemap_has_100_urls():
    assert len(city_sitemap()) == 100


def test_sitemap_example_from_a_big_city_uses_the_city_prefix():
    urls = city_sitemap()
    example = next(u for u in urls if "/job/PARIS/" in u)
    result = count_job_links(urls, SITEMAP_URL, example)
    assert result.count == 65
    assert result.shape == "jobs.example.test/careers/job/PARIS/*"


def test_sitemap_example_from_a_small_city_falls_back_to_the_whole_job_folder():
    """A rule of 'path minus the last segment' would have found only 4 here."""
    urls = city_sitemap()
    example = next(u for u in urls if "/job/DIJON/" in u)
    result = count_job_links(urls, SITEMAP_URL, example)
    assert result.count == 100
    assert result.shape == "jobs.example.test/careers/job/*/*"


# -- the rule on synthetic links -----------------------------------------------------------------------------------

PAGE = "https://x.test/en/careers/"


def links(prefix: str, count: int, start: int = 1) -> list[str]:
    return [f"{prefix}{n}" for n in range(start, start + count)]


def test_exactly_five_matching_links_are_enough_and_four_are_not():
    example = "https://x.test/en/jobs/100"
    assert count_job_links(links("/en/jobs/", 5), PAGE, example).count == 5
    four = count_job_links(links("/en/jobs/", 4), PAGE, example)
    assert four.count == 0 and "only 4" in four.note and str(MIN_JOB_LINKS) in four.note


def test_the_floor_of_two_segments_stops_menu_links_counting_as_jobs():
    """Three real jobs and fifty menu links: the menu must not be taken for jobs."""
    menu = links("/en/about/", 50)
    jobs = links("/en/jobs/", 3)
    result = count_job_links(menu + jobs, PAGE, "https://x.test/en/jobs/100")
    assert result.count == 0 and "only 3" in result.note


def test_the_listing_page_itself_and_other_depths_are_not_counted():
    mixed = ["/en/jobs/", "/en/jobs", "/en/jobs/a/b"] + links("/en/jobs/", 5)
    assert count_job_links(mixed, PAGE, "https://x.test/en/jobs/100").count == 5


def test_other_hosts_are_not_counted():
    result = count_job_links(links("https://other.test/en/jobs/", 9), PAGE, "https://x.test/en/jobs/100")
    assert result.count == 0


def test_relative_links_are_resolved_fragments_dropped_and_duplicates_counted_once():
    hrefs = links("/en/jobs/", 5) + ["/en/jobs/1#top", "https://x.test/en/jobs/2", "../jobs/3"]
    assert count_job_links(hrefs, PAGE, "https://x.test/en/jobs/100").count == 5


def test_http_and_https_versions_of_a_link_are_both_seen_but_scheme_is_ignored_for_the_shape():
    hrefs = ["http://x.test/en/jobs/1", "https://x.test/en/jobs/2", "http://x.test/en/jobs/3",
             "https://x.test/en/jobs/4", "http://x.test/en/jobs/5"]
    assert count_job_links(hrefs, PAGE, "https://x.test/en/jobs/100").count == 5


def test_mailto_javascript_and_tel_links_are_ignored():
    hrefs = ["mailto:a@x.test", "javascript:void(0)", "tel:123", "#", ""] + links("/en/jobs/", 5)
    assert count_job_links(hrefs, PAGE, "https://x.test/en/jobs/100").count == 5


def test_an_id_in_the_query_needs_the_same_parameter_names():
    jobs = [f"/jobs?id={n}" for n in range(1, 7)]
    pagination = [f"/jobs?page={n}" for n in range(1, 9)]
    result = count_job_links(jobs + pagination, "https://x.test/", "https://x.test/jobs?id=5")
    assert result.count == 6
    assert result.shape == "x.test/jobs?id=*"


def test_pagination_alone_is_not_taken_for_jobs():
    pagination = [f"/jobs?page={n}" for n in range(1, 9)]
    assert count_job_links(pagination, "https://x.test/", "https://x.test/jobs?id=5").count == 0


def test_a_tracking_parameter_on_candidates_is_harmless_when_the_example_has_no_query():
    hrefs = [f"/en/jobs/{n}?utm=mail" for n in range(1, 6)]
    assert count_job_links(hrefs, PAGE, "https://x.test/en/jobs/100").count == 5


@pytest.mark.parametrize("example, reason", [
    ("https://x.test/100", "no path segment"),        # one segment and no query: nothing fixed to match
    ("https://x.test/", "no path segment"),
    ("not a url", "not a usable"),
    ("ftp://x.test/a/b", "not a usable"),
])
def test_examples_the_rule_cannot_use_say_why(example, reason):
    result = count_job_links(links("/en/jobs/", 9), PAGE, example)
    assert result.count == 0 and reason in result.note


# -- analyze_listing ------------------------------------------------------------------------------------------------

def page_with(links_html: str, root: str = "") -> str:
    return f"<html><body>{root}{links_html}</body></html>"


def anchors(prefix: str, count: int) -> str:
    return "".join(f'<a href="{prefix}{n}">job {n}</a>' for n in range(count))


def test_job_links_evidence_has_the_kind_url_and_a_readable_detail():
    findings = analyze_listing(PAGE, page_with(anchors("/en/jobs/", 6)), "https://x.test/en/jobs/100")
    (evidence,) = findings.evidence
    assert evidence.kind == "job_links" and evidence.url == PAGE
    assert evidence.detail == "6 distinct links match x.test/en/jobs/* (shape of the example job URL)"


def test_no_example_means_a_note_and_no_job_links():
    findings = analyze_listing(PAGE, page_with(anchors("/en/jobs/", 9)))
    assert [e.kind for e in findings.evidence] == ["note"]
    assert "no example job URL" in findings.evidence[0].detail and findings.job_link_count == 0


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_blank_example_counts_as_no_example(blank):
    assert analyze_listing(PAGE, "<p>x</p>", blank).evidence[0].kind == "note"


def test_an_empty_root_without_an_example_is_still_recognised_as_an_app_shell():
    findings = analyze_listing(PAGE, '<div id="root"></div>')
    assert [e.kind for e in findings.evidence] == ["note", "spa_shell"]


def test_an_empty_root_next_to_real_job_links_is_not_an_app_shell():
    text = page_with(anchors("/en/jobs/", 6), root='<div id="root"></div>')
    findings = analyze_listing(PAGE, text, "https://x.test/en/jobs/100")
    assert [e.kind for e in findings.evidence] == ["job_links"]


def test_a_page_with_content_in_its_root_is_not_an_app_shell():
    findings = analyze_listing(PAGE, '<div id="root"><h1>Jobs</h1></div>', "https://x.test/en/jobs/100")
    assert "spa_shell" not in [e.kind for e in findings.evidence]


def test_evidence_kinds_and_lengths_always_follow_the_contract():
    long_example = "https://x.test/" + "a/" * 200 + "b"
    for example in (None, "x", long_example, "https://x.test/en/jobs/1"):
        for text in ("", '<div id="root"></div>', page_with(anchors("/en/jobs/", 7))):
            for item in analyze_listing(PAGE, text, example).evidence:
                assert item.kind in EVIDENCE_KINDS and len(item.detail) <= MAX_DETAIL
