"""Offline tests for the paged-card adapter."""

import json

from tracker.adapters.paged_cards import check_paged_cards
from tracker.triage.fetcher import Fetcher
from tracker.triage.transport import TransportError
from fake_transport import FakeClock, FakeTransport, reply, robots_ok


LIST_URL = "https://jobs.example.test/list"
API_TEMPLATE = "https://jobs.example.test/api?page={page}"


def card(card_id: str, title: str | None = None) -> str:
    title = title or f"Example Intern {card_id}"
    return (
        '<div class="job-card" id="'
        + card_id
        + '"><div class="job-card-overlay"><a href="https://jobs.example.test/job/'
        + card_id
        + '"></a></div><div class="job-card-title">'
        + title
        + '</div><div class="icon city">Example City</div>'
        '<div class="icon contract-type">Example Contract</div>'
        '<div class="icon employment-type">Example Time</div>'
        '<span class="Badge">Example Category</span></div>'
    )


def html(cards: str, count: int) -> str:
    return f'<div class="cards-container"><div class="jobs-count">{count}</div>{cards}</div>'


def fetcher(transport: FakeTransport) -> Fetcher:
    clock = transport.clock or FakeClock()
    return Fetcher(
        transport,
        "InternshipTracker/0.1 (test; contact test@example.test)",
        sleep=clock.sleep,
        clock=clock,
    )


def api(cards: str, count: int, end: bool) -> str:
    return json.dumps(
        {
            "html": html(cards, count),
            "jobsCount": count,
            "nothingMoreToDisplay": end,
        }
    )


def test_collects_pages_and_maps_listing_fields():
    transport = FakeTransport()
    transport.add(
        "https://jobs.example.test/robots.txt",
        robots_ok(),
    ).add(
        LIST_URL,
        reply(body=html(card("a") + card("b"), 4)),
    ).add(
        API_TEMPLATE.format(page=2),
        reply(body=api(card("c") + card("d"), 2, True), ctype="application/json"),
    )

    result = check_paged_cards(
        fetcher(transport),
        {"list_url": LIST_URL, "api_url_template": API_TEMPLATE},
    )

    assert result.status == "ok"
    assert [listing.source_job_id for listing in result.listings] == ["a", "b", "c", "d"]
    assert result.listings[0].description == (
        "Example Category | Example Contract | Example Time"
    )
    assert result.evidence == ("pages=2", "cards=4", "announced=4", "duplicates=0")


def test_requests_all_pages_until_end_marker():
    transport = FakeTransport()
    transport.add("https://jobs.example.test/robots.txt", robots_ok())
    transport.add(LIST_URL, reply(body=html(card("a"), 3)))
    transport.add(
        API_TEMPLATE.format(page=2),
        reply(body=api(card("b"), 1, False), ctype="application/json"),
    )
    transport.add(
        API_TEMPLATE.format(page=3),
        reply(body=api(card("c"), 1, True), ctype="application/json"),
    )

    result = check_paged_cards(
        fetcher(transport),
        {"list_url": LIST_URL, "api_url_template": API_TEMPLATE},
    )

    assert result.status == "ok"
    assert transport.calls[-2:] == [
        API_TEMPLATE.format(page=2),
        API_TEMPLATE.format(page=3),
    ]


def test_api_page_three_failure_discards_previous_pages():
    transport = FakeTransport()
    transport.add("https://jobs.example.test/robots.txt", robots_ok())
    transport.add(LIST_URL, reply(body=html(card("a"), 3)))
    transport.add(
        API_TEMPLATE.format(page=2),
        reply(body=api(card("b"), 1, False), ctype="application/json"),
    )
    transport.add(
        API_TEMPLATE.format(page=3),
        TransportError("timeout", "Example timeout"),
    )

    result = check_paged_cards(
        fetcher(transport),
        {"list_url": LIST_URL, "api_url_template": API_TEMPLATE},
    )

    assert result.status == "failed"
    assert result.listings == ()
    assert result.error.startswith("timeout:")


def test_cross_page_duplicate_is_deduplicated_before_count_check():
    transport = FakeTransport()
    transport.add("https://jobs.example.test/robots.txt", robots_ok())
    transport.add(LIST_URL, reply(body=html(card("a"), 3)))
    transport.add(
        API_TEMPLATE.format(page=2),
        reply(body=api(card("a") + card("b"), 2, True), ctype="application/json"),
    )

    result = check_paged_cards(
        fetcher(transport),
        {"list_url": LIST_URL, "api_url_template": API_TEMPLATE},
    )

    assert result.status == "failed"
    assert result.error == (
        "incomplete or inconsistent list: collected 2 unique cards, announced 3"
    )


def test_blocking_or_failure_discards_partial_listings():
    transport = FakeTransport()
    transport.add(
        "https://jobs.example.test/robots.txt",
        robots_ok(),
    ).add(
        LIST_URL,
        reply(body=html(card("a"), 2)),
    ).add(
        API_TEMPLATE.format(page=2),
        reply(429, body="blocked"),
    )

    result = check_paged_cards(
        fetcher(transport),
        {"list_url": LIST_URL, "api_url_template": API_TEMPLATE},
    )

    assert result.status == "blocked"
    assert result.listings == ()
    assert "http_429" in result.error


def test_layout_count_and_json_errors_are_failed():
    transport = FakeTransport()
    transport.add("https://jobs.example.test/robots.txt", robots_ok())
    transport.add(LIST_URL, reply(body="<main>not a list</main>"))
    result = check_paged_cards(
        fetcher(transport),
        {"list_url": LIST_URL, "api_url_template": API_TEMPLATE},
    )
    assert result.status == "failed"
    assert result.error == "unexpected page layout: no job list found"

    transport = FakeTransport()
    transport.add("https://jobs.example.test/robots.txt", robots_ok())
    transport.add(LIST_URL, reply(body=html(card("a"), 2)))
    transport.add(API_TEMPLATE.format(page=2), reply(body="{bad", ctype="application/json"))
    result = check_paged_cards(
        fetcher(transport),
        {"list_url": LIST_URL, "api_url_template": API_TEMPLATE},
    )
    assert result.status == "failed"
    assert result.error.startswith("unexpected API response")


def test_mismatch_duplicates_empty_page_and_page_limit_are_failed():
    transport = FakeTransport()
    transport.add("https://jobs.example.test/robots.txt", robots_ok())
    transport.add(LIST_URL, reply(body=html(card("a") + card("a"), 2)))
    transport.add(
        API_TEMPLATE.format(page=2),
        reply(body=api(card("b"), 1, True), ctype="application/json"),
    )
    result = check_paged_cards(
        fetcher(transport),
        {"list_url": LIST_URL, "api_url_template": API_TEMPLATE},
    )
    assert result.status == "ok"
    assert result.evidence[-1] == "duplicates=1"

    transport = FakeTransport()
    transport.add("https://jobs.example.test/robots.txt", robots_ok())
    transport.add(LIST_URL, reply(body=html(card("a"), 2)))
    transport.add(
        API_TEMPLATE.format(page=2),
        reply(body=api("", 0, False), ctype="application/json"),
    )
    result = check_paged_cards(
        fetcher(transport),
        {"list_url": LIST_URL, "api_url_template": API_TEMPLATE},
    )
    assert result.error == "empty page without end marker"

    transport = FakeTransport()
    transport.add("https://jobs.example.test/robots.txt", robots_ok())
    transport.add(LIST_URL, reply(body=html(card("a"), 2)))
    transport.add(
        API_TEMPLATE.format(page=2),
        reply(body=api(card("b"), 1, False), ctype="application/json"),
    )
    result = check_paged_cards(
        fetcher(transport),
        {"list_url": LIST_URL, "api_url_template": API_TEMPLATE, "max_pages": 1},
    )
    assert result.error == "too many pages: no end marker after 1 pages"


def test_zero_count_and_parse_problem_are_reported():
    transport = FakeTransport()
    transport.add("https://jobs.example.test/robots.txt", robots_ok())
    transport.add(LIST_URL, reply(body=html("", 0)))
    result = check_paged_cards(
        fetcher(transport),
        {"list_url": LIST_URL, "api_url_template": API_TEMPLATE},
    )
    assert result.status == "empty"
    assert result.evidence == ("pages=1", "announced=0")

    bad_card = '<div class="job-card" id="bad"><div class="job-card-title">Missing link</div></div>'
    transport = FakeTransport()
    transport.add("https://jobs.example.test/robots.txt", robots_ok())
    transport.add(LIST_URL, reply(body=html(bad_card, 1)))
    transport.add(
        API_TEMPLATE.format(page=2),
        reply(body=api("", 0, True), ctype="application/json"),
    )
    result = check_paged_cards(
        fetcher(transport),
        {"list_url": LIST_URL, "api_url_template": API_TEMPLATE},
    )
    assert result.status == "failed"
    assert "missing link" in result.error
