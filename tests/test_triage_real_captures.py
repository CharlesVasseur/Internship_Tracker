"""Checks of the analyzers on the author's REAL saved pages and sitemaps.

Those files are browser captures kept on the author's machine in
tests/local_fixtures/. They are not in the repository (git ignores that folder),
because the tracker itself is not specific to any firm. Without them, every test
here is skipped, with the reason shown by `pytest -rs`.

tests/local_fixtures/manifest.json says, for each file, the page URL, an example
job URL taken from that same capture, and what the analyzers must find.
"""

import json
import re
from pathlib import Path

import pytest

from tracker.triage.listing import analyze_listing, count_job_links
from tracker.triage.sitemap import read_sitemap
from tracker.triage.terms import analyze_terms
from tracker.triage.vendors import analyze_vendors

LOCAL = Path(__file__).parent / "local_fixtures"
MANIFEST = LOCAL / "manifest.json"

pytestmark = pytest.mark.skipif(
    not MANIFEST.exists(),
    reason="real captures not present: tests/local_fixtures/ stays on the author's machine, not in the repository",
)


def cases(kind: str) -> list[dict]:
    if not MANIFEST.exists():
        return []
    return [case for case in json.loads(MANIFEST.read_text(encoding="utf-8")) if case["kind"] == kind]


def read(name: str) -> str:
    return (LOCAL / name).read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("case", cases("listing"), ids=lambda case: case["file"])
def test_real_listing_page(case):
    findings = analyze_listing(case["page_url"], read(case["file"]), case["example_job_url"])
    assert findings.job_link_count == case["expect_job_links"]
    kinds = [item.kind for item in findings.evidence]
    assert ("spa_shell" in kinds) == case["expect_spa_shell"]


NEUTRAL_PAGE = "https://neutral.example.test/careers"


@pytest.mark.parametrize("case", cases("listing"), ids=lambda case: case["file"])
def test_real_listing_vendor_hosts_are_found_inside_the_page(case):
    """A neutral page address is used, so the vendor host must come from the page's own tags."""
    found = analyze_vendors(NEUTRAL_PAGE, read(case["file"]))
    assert {item.detail.split(":")[0] for item in found} == set(case["expect_vendors"])


@pytest.mark.parametrize("case", cases("sitemap_shape"), ids=lambda case: case["file"])
def test_real_sitemap_follows_the_shape_rule(case):
    urls = re.findall(r"<loc>\s*(.*?)\s*</loc>", read(case["file"]))
    for probe in case["probes"]:
        example = next(url for url in urls if probe["contains"] in url)
        assert count_job_links(urls, case["page_url"], example).count == probe["expect"]


@pytest.mark.parametrize("case", cases("sitemap_read"), ids=lambda case: case["file"])
def test_real_sitemap_is_read_as_expected(case):
    text = read(case["file"])
    example = None
    if case["example_contains"]:
        example = next(url for url in re.findall(r"<loc>\s*(.*?)\s*</loc>", text) if case["example_contains"] in url)
    found = read_sitemap(case["page_url"], text, example)
    assert found.kind == case["expect_kind"]
    assert len(found.urls) == case["expect_urls"] and found.job_url_count == case["expect_job_urls"]
    assert found.evidence[0].kind == case["expect_evidence"]


@pytest.mark.parametrize("case", cases("terms"), ids=lambda case: case["file"])
def test_real_terms_page_gives_the_expected_flag(case):
    flag = analyze_terms(case["page_url"], read(case["file"]))
    assert flag.status == case["expect_status"]
    assert flag.language_guess == case["expect_language"]
    if case["expect_quote_contains"]:
        assert any(case["expect_quote_contains"] in quote for quote in flag.quotes)
    else:
        assert flag.quotes == ()                      # a real page with no ban on automated access
