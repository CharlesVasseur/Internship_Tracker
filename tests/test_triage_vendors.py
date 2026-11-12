"""Tests for vendors.py (step T3.2). Offline, firm-neutral: every host here is synthetic."""

import pytest

from tracker.triage.contract import EVIDENCE_KINDS
from tracker.triage.html_scan import scan_html
from tracker.triage.vendors import MAX_VENDOR_EVIDENCE, VENDORS, analyze_vendors, find_vendors, vendor_for_host

PAGE = "https://www.example.test/careers"

SPEC_LABELS = {"myworkdayjobs", "oraclecloud", "csod", "softgarden", "yello", "ultipro",
               "successfactors", "icims"}


# -- the table ----------------------------------------------------------------------------------

def test_the_table_holds_every_vendor_named_in_the_spec_and_each_says_how_it_was_verified():
    labels = {vendor.label for vendor in VENDORS}
    assert SPEC_LABELS <= labels and "phenompeople" in labels
    for vendor in VENDORS:
        assert vendor.name and vendor.verified.startswith("survey:")


def test_labels_and_names_are_unique():
    assert len({v.label for v in VENDORS}) == len(VENDORS)
    assert len({v.name for v in VENDORS}) == len(VENDORS)


# -- matching a host ----------------------------------------------------------------------------------

@pytest.mark.parametrize("host, expected", [
    ("tenant.wd5.myworkdayjobs.com", "Workday"),
    ("tenant.myworkdayjobs.com", "Workday"),
    ("a.b.fa.oraclecloud.com", "Oracle Cloud"),
    ("jobs.phenompeople.com", "Phenom"),
    ("recruiting.ultipro.com", "UltiPro"),
    ("firm.csod.com", "csod"),
    ("firm.softgarden.io", "softgarden"),
    ("firm.softgarden.de", "softgarden"),        # same vendor, another ending
    ("group.yello.co", "yello"),
    ("career5.successfactors.eu", "SuccessFactors"),
    ("careers-firm.icims.com", "icims"),
    ("TENANT.MyWorkdayJobs.COM", "Workday"),
])
def test_known_vendor_hosts_are_recognised(host, expected):
    assert vendor_for_host(host).name == expected


@pytest.mark.parametrize("host", [
    "www.example.test", "myworkdayjobs-fake.example.test", "notmyworkdayjobs.com",
    "example.test", "csodx.com", "", "localhost",
])
def test_other_hosts_are_not_vendors(host):
    assert vendor_for_host(host) is None


# -- where hosts are looked for ------------------------------------------------------------------------------------

def where(html: str) -> list[str]:
    return [e.detail for e in analyze_vendors(PAGE, html)]


def test_an_anchor_a_link_tag_a_script_and_a_form_are_all_checked():
    html = ('<a href="https://t.softgarden.io/job/1">x</a>'
            '<link rel="canonical" href="https://t.wd5.myworkdayjobs.com/site">'
            '<script src="https://cdn.phenompeople.com/app.js"></script>'
            '<form action="https://x.icims.com/apply"></form>')
    details = where(html)
    assert details == [
        "Workday: t.wd5.myworkdayjobs.com (seen in link)",
        "Phenom: cdn.phenompeople.com (seen in script)",
        "icims: x.icims.com (seen in form)",
        "softgarden: t.softgarden.io (seen in anchor)",
    ]


def test_the_page_address_itself_counts():
    found = find_vendors("https://t.wd5.myworkdayjobs.com/en/site", scan_html("<p>x</p>"))
    assert [e.detail for e in found] == ["Workday: t.wd5.myworkdayjobs.com (seen in page address)"]


def test_extra_places_can_be_checked_too():
    found = find_vendors(PAGE, scan_html("<p>x</p>"), (("example job URL", "https://t.softgarden.io/job/9"),))
    assert [e.detail for e in found] == ["softgarden: t.softgarden.io (seen in example job URL)"]


def test_the_same_vendor_host_is_reported_once_but_two_hosts_of_one_vendor_are_two_findings():
    html = ('<a href="https://a.softgarden.io/job/1">1</a><a href="https://a.softgarden.io/job/2">2</a>'
            '<a href="https://b.softgarden.io/job/3">3</a>')
    assert where(html) == ["softgarden: a.softgarden.io (seen in anchor)",
                           "softgarden: b.softgarden.io (seen in anchor)"]


def test_relative_links_belong_to_the_page_host_not_to_a_vendor():
    assert where('<a href="/job/1">x</a><a href="job/2">y</a>') == []


def test_broken_urls_are_skipped_without_error():
    assert where('<a href="http://[bad">x</a><a href="">y</a><link href="::::">') == []


def test_the_number_of_findings_is_capped():
    hosts = "".join(f'<a href="https://t{n}.softgarden.io/j">x</a>' for n in range(20))
    assert len(where(hosts)) == MAX_VENDOR_EVIDENCE


def test_evidence_is_vendor_host_kind_with_the_page_as_url():
    (evidence,) = analyze_vendors(PAGE, '<a href="https://t.csod.com/x">x</a>')
    assert evidence.kind == "vendor_host" and evidence.kind in EVIDENCE_KINDS and evidence.url == PAGE


def test_no_vendor_means_no_evidence_and_no_claim():
    assert analyze_vendors(PAGE, '<a href="https://www.other.test/">x</a>') == []


def test_a_broken_address_in_any_place_is_skipped_not_fatal():
    html = '<a href="http://[bad">x</a><script src="//[y"></script><a href="https://t.csod.com/x">ok</a>'
    assert [e.detail for e in analyze_vendors(PAGE, html)] == ["csod: t.csod.com (seen in anchor)"]
