"""Tests for src/tracker/triage/fetcher.py and fetch_result.py (step T2.4). Offline.

Every answer is scripted through FakeTransport; time is a FakeClock that only
moves when the fetcher sleeps. Bodies are SYNTHETIC. Nothing contacts a site.
"""

import gzip
import random

import pytest

from fake_transport import (
    FakeClock,
    FakeTransport,
    UnscriptedRequest,
    redirect,
    reply,
    robots_ok,
)
from conftest import NetworkAttempt
from tracker.triage.cache import CacheMiss, RecordingTransport, ReplayTransport, ResponseCache
from tracker.triage.contract import ERROR_KINDS, BlockInfo, ContractError, TriageError
from tracker.triage.fetch_result import FetchResult
from tracker.triage.fetcher import Fetcher
from tracker.triage.robots import RobotsRules
from tracker.triage.sitemap import read_sitemap
from tracker.triage.transport import RawResponse, TransportError

UA = "InternshipTracker/0.1 (triage; contact test@example.com)"
ROBOTS = "https://x.test/robots.txt"
PAGE = "https://x.test/jobs"
OTHER = "https://x.test/other"


def make(transport, **options):
    clock = FakeClock()
    if transport.clock is None:
        transport.clock = clock
    fetcher = Fetcher(transport, UA, sleep=clock.sleep, clock=clock, **options)
    return fetcher, clock


def simple(page_answer=None, robots=None):
    """A transport with robots (allow all by default) and one page."""
    transport = FakeTransport()
    transport.add(ROBOTS, robots if robots is not None else robots_ok())
    transport.add(PAGE, page_answer if page_answer is not None else reply(200, "<html>jobs</html>"))
    return transport


# -- the happy path ---------------------------------------------------------------------------

def test_robots_is_fetched_first_then_the_page():
    transport = simple()
    fetcher, _ = make(transport)
    result = fetcher.fetch(PAGE)
    assert result.status == "ok" and result.text == "<html>jobs</html>"
    assert transport.calls == [ROBOTS, PAGE]
    assert result.requests == (ROBOTS, PAGE)
    assert (result.url, result.final_url, result.http_status) == (PAGE, PAGE, 200)
    assert result.redirect_chain == ()


def test_every_request_names_the_project_and_asks_for_compression_with_a_15s_timeout():
    transport = simple()
    make(transport)[0].fetch(PAGE)
    for headers in transport.sent_headers:
        assert headers["User-Agent"] == UA and "InternshipTracker" in headers["User-Agent"]
        assert headers["Accept-Encoding"] == "gzip, deflate"
    assert set(transport.timeouts) == {15}


def test_robots_is_fetched_once_per_site_and_later_results_list_only_their_own_requests():
    transport = simple().add(OTHER, reply(200, "other"))
    fetcher, _ = make(transport)
    fetcher.fetch(PAGE)
    second = fetcher.fetch(OTHER)
    assert transport.calls.count(ROBOTS) == 1
    assert second.requests == (OTHER,)
    assert fetcher.request_log == (ROBOTS, PAGE, OTHER)


def test_robots_rules_can_be_read_after_a_fetch():
    fetcher, _ = make(simple(robots=robots_ok("User-agent: *\nCrawl-delay: 2\nSitemap: https://x.test/s.xml")))
    assert fetcher.robots_rules(PAGE) is None
    fetcher.fetch(PAGE)
    rules = fetcher.robots_rules(PAGE)
    assert isinstance(rules, RobotsRules)
    assert rules.crawl_delay == 2.0 and rules.sitemaps == ("https://x.test/s.xml",)


# -- waiting -----------------------------------------------------------------------------------------

def test_at_least_one_second_between_requests_to_the_same_host():
    transport = simple().add(OTHER, reply(200, "o"))
    fetcher, clock = make(transport)
    fetcher.fetch(PAGE)
    fetcher.fetch(OTHER)
    gaps = [b - a for a, b in zip(transport.times, transport.times[1:])]
    assert all(gap >= 1.0 for gap in gaps)
    assert clock.sleeps == [1.0, 1.0]


def test_other_hosts_are_not_made_to_wait():
    transport = simple()
    transport.add("https://y.test/robots.txt", robots_ok()).add("https://y.test/p", reply(200, "y"))
    fetcher, clock = make(transport)
    fetcher.fetch(PAGE)
    fetcher.fetch("https://y.test/p")
    x_robots, x_page, y_robots, y_page = transport.times
    assert y_robots == x_page             # y.test's first request did not wait for x.test
    assert (x_page - x_robots, y_page - y_robots) == (1.0, 1.0)   # each host keeps its own 1 s gap
    assert clock.sleeps == [1.0, 1.0]


def test_a_crawl_delay_is_honoured_between_pages():
    transport = simple(robots=robots_ok("User-agent: *\nCrawl-delay: 5")).add(OTHER, reply(200, "o"))
    fetcher, clock = make(transport)
    fetcher.fetch(PAGE)
    fetcher.fetch(OTHER)
    assert clock.sleeps == [5.0, 5.0]


def test_a_crawl_delay_over_the_cap_is_not_waited_for_and_says_so():
    transport = simple(robots=robots_ok("User-agent: *\nCrawl-delay: 30"))
    fetcher, clock = make(transport)
    result = fetcher.fetch(PAGE)
    assert result.status == "failed" and result.error.kind == "budget"
    assert result.error.retryable is True and "30" in result.error.message
    assert transport.calls == [ROBOTS] and clock.sleeps == []


def test_the_cap_is_adjustable():
    transport = simple(robots=robots_ok("User-agent: *\nCrawl-delay: 30"))
    fetcher, clock = make(transport, max_crawl_delay=60)
    assert fetcher.fetch(PAGE).status == "ok"
    assert clock.sleeps == [30.0]


# -- robots.txt outcomes -------------------------------------------------------------------------------

def test_a_robots_disallow_blocks_that_url_but_not_the_whole_host():
    transport = simple(robots=robots_ok("User-agent: *\nDisallow: /jobs")).add(OTHER, reply(200, "o"))
    fetcher, _ = make(transport)
    blocked = fetcher.fetch(PAGE)
    assert blocked.status == "blocked"
    assert (blocked.block.reason, blocked.block.http_status, blocked.block.url) == ("robots_disallow", None, PAGE)
    assert PAGE not in transport.calls
    assert fetcher.fetch(OTHER).status == "ok"


@pytest.mark.parametrize("status", [404, 410])
def test_no_robots_file_means_no_rules(status):
    transport = simple(robots=reply(status, "not found"))
    assert make(transport)[0].fetch(PAGE).status == "ok"


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_refused_robots_file_blocks_the_host_and_stops_asking(status):
    transport = simple(robots=reply(status, "no")).add(OTHER, reply(200, "o"))
    fetcher, clock = make(transport)
    first = fetcher.fetch(PAGE)
    assert first.status == "blocked"
    assert (first.block.reason, first.block.http_status, first.block.url) == (f"http_{status}", status, ROBOTS)
    assert transport.calls == [ROBOTS]            # never retried
    second = fetcher.fetch(OTHER)
    assert second.status == "blocked" and transport.calls == [ROBOTS]
    assert clock.sleeps == []


def test_a_server_error_on_robots_is_retried_then_robots_unreachable():
    transport = simple(robots=reply(500))
    fetcher, clock = make(transport)
    result = fetcher.fetch(PAGE)
    assert result.status == "failed" and result.error.kind == "robots_unreachable"
    assert result.error.retryable is True and "500" in result.error.message
    assert transport.calls == [ROBOTS] * 3 and clock.sleeps == [2.0, 4.0]
    again = fetcher.fetch(PAGE)                   # remembered: nothing is hammered
    assert again.error.kind == "robots_unreachable" and transport.calls == [ROBOTS] * 3


def test_repeated_timeouts_on_robots_note_a_possible_silent_block():
    transport = simple(robots=TransportError("timeout", "timed out"))
    result = make(transport)[0].fetch(PAGE)
    assert result.error.kind == "robots_unreachable"
    assert "silently blocking" in result.error.message and "timed out" in result.error.message
    assert PAGE not in transport.calls


def test_a_tls_error_on_robots_is_one_attempt_and_not_retryable():
    transport = simple(robots=TransportError("tls", "certificate verify failed"))
    fetcher, clock = make(transport)
    result = fetcher.fetch(PAGE)
    assert result.error.kind == "tls" and result.error.retryable is False
    assert transport.calls == [ROBOTS] and clock.sleeps == []


def test_a_dns_error_on_robots_keeps_its_own_kind_after_retries():
    transport = simple(robots=TransportError("dns", "no such host"))
    result = make(transport)[0].fetch(PAGE)
    assert result.error.kind == "dns" and transport.calls == [ROBOTS] * 3


def test_other_4xx_on_robots_is_a_visible_failure():
    result = make(simple(robots=reply(400)))[0].fetch(PAGE)
    assert result.error.kind == "http_4xx" and "robots.txt" in result.error.message


def test_an_html_page_instead_of_robots_is_failed_decode_and_nothing_is_fetched():
    transport = simple(robots=reply(200, "<!DOCTYPE html><title>404: Not Found</title>"))
    result = make(transport)[0].fetch(PAGE)
    assert result.error.kind == "decode" and "robots" in result.error.message
    assert transport.calls == [ROBOTS]


def test_a_non_text_robots_is_failed_not_text():
    result = make(simple(robots=reply(200, b"%PDF", ctype="application/pdf")))[0].fetch(PAGE)
    assert result.error.kind == "not_text" and result.error.message.startswith("robots.txt:")


def test_robots_may_redirect_to_another_origin():
    transport = FakeTransport()
    transport.add("http://x.test/robots.txt", redirect("https://x.test/robots.txt"))
    transport.add(ROBOTS, robots_ok())
    transport.add("http://x.test/jobs", reply(200, "plain http page"))
    result = make(transport)[0].fetch("http://x.test/jobs")
    assert result.status == "ok"
    assert transport.calls == ["http://x.test/robots.txt", ROBOTS, "http://x.test/jobs"]


def test_a_robots_redirect_loop_is_reported():
    transport = FakeTransport()
    transport.add(ROBOTS, redirect("/robots2.txt"))
    transport.add("https://x.test/robots2.txt", redirect("/robots.txt"))
    result = make(transport)[0].fetch(PAGE)
    assert result.error.kind == "redirect_loop"


# -- page outcomes: 4xx, blocks, 5xx ------------------------------------------------------------------

@pytest.mark.parametrize("status", [400, 404, 410])
def test_a_4xx_page_is_a_visible_failure_not_retried(status):
    transport = simple(reply(status, "gone"))
    result = make(transport)[0].fetch(PAGE)
    assert result.status == "failed" and result.text is None
    assert result.error.kind == "http_4xx" and result.error.retryable is False
    assert (result.http_status, result.error.url) == (status, PAGE)
    assert transport.calls.count(PAGE) == 1


@pytest.mark.parametrize("status", [401, 403, 429])
def test_401_403_429_are_blocked_never_retried_and_stop_the_host(status):
    transport = simple(reply(status, "no")).add(OTHER, reply(200, "o"))
    transport.add("https://y.test/robots.txt", robots_ok()).add("https://y.test/p", reply(200, "y"))
    fetcher, _ = make(transport)
    first = fetcher.fetch(PAGE)
    assert first.status == "blocked" and first.http_status == status
    assert first.block == BlockInfo(f"http_{status}", PAGE, status)
    assert transport.calls.count(PAGE) == 1
    later = fetcher.fetch(OTHER)
    assert later.status == "blocked" and OTHER not in transport.calls
    assert fetcher.fetch("https://y.test/p").status == "ok"      # other hosts are unaffected


def test_a_server_error_is_retried_with_waits_then_reported():
    transport = simple(reply(500))
    result = make(transport)[0].fetch(PAGE)
    assert result.error.kind == "http_5xx" and result.error.retryable is True
    assert transport.calls.count(PAGE) == 3 and "3 attempts" in result.error.message


def test_retry_waits_are_two_then_four_seconds():
    fetcher, clock = make(simple(reply(503)))
    fetcher.fetch(PAGE)
    assert clock.sleeps == [1.0, 2.0, 4.0]       # 1.0 is the normal gap after robots.txt


def test_a_page_that_recovers_after_a_server_error_is_ok():
    transport = simple().add(PAGE, reply(503), reply(200, "back"))
    fetcher, clock = make(transport)
    result = fetcher.fetch(PAGE)
    assert result.status == "ok" and result.text == "back"
    assert result.requests == (ROBOTS, PAGE, PAGE) and clock.sleeps == [1.0, 2.0]


def test_repeated_timeouts_are_failed_with_a_silent_block_note():
    transport = simple(TransportError("timeout", "timed out"))
    result = make(transport)[0].fetch(PAGE)
    assert result.error.kind == "timeout" and result.error.retryable is True
    assert "silently blocking" in result.error.message and transport.calls.count(PAGE) == 3


def test_a_timeout_then_success_is_ok():
    transport = simple().add(PAGE, TransportError("timeout", "slow"), reply(200, "fine"))
    assert make(transport)[0].fetch(PAGE).text == "fine"


def test_connection_and_dns_errors_are_retried():
    for kind in ("connection", "dns"):
        transport = simple(TransportError(kind, "boom"))
        result = make(transport)[0].fetch(PAGE)
        assert result.error.kind == kind and transport.calls.count(PAGE) == 3


def test_a_tls_error_on_a_page_is_one_attempt_and_never_retried():
    transport = simple(TransportError("tls", "self-signed certificate in certificate chain"))
    result = make(transport)[0].fetch(PAGE)
    assert result.error.kind == "tls" and result.error.retryable is False
    assert transport.calls.count(PAGE) == 1 and "self-signed" in result.error.message


@pytest.mark.parametrize("url", ["", "not a url", "ftp://x.test/f", "http:///nohost", "https://x.test:99999/"])
def test_unusable_urls_fail_without_any_request(url):
    transport = FakeTransport()
    result = make(transport)[0].fetch(url)
    assert result.status == "failed" and result.error.kind == "invalid_url"
    assert transport.calls == [] and result.requests == ()


def test_an_unexpected_status_is_reported_not_retried_or_guessed():
    transport = simple(reply(999))
    result = make(transport)[0].fetch(PAGE)
    assert result.error.kind == "internal" and "999" in result.error.message
    assert transport.calls.count(PAGE) == 1


# -- redirects -----------------------------------------------------------------------------------------------

def test_a_relative_redirect_is_followed_and_recorded():
    transport = simple(redirect("/new")).add("https://x.test/new", reply(200, "moved here"))
    result = make(transport)[0].fetch(PAGE)
    assert result.status == "ok" and result.text == "moved here"
    assert result.final_url == "https://x.test/new" and result.redirect_chain == ("https://x.test/new",)
    assert result.url == PAGE


def test_a_fragment_is_dropped_from_the_redirect_target():
    transport = simple(redirect("/new#top")).add("https://x.test/new", reply(200, "x"))
    assert make(transport)[0].fetch(PAGE).final_url == "https://x.test/new"


def test_a_cross_host_redirect_checks_the_new_hosts_robots_first():
    transport = simple(redirect("https://y.test/jobs"))
    transport.add("https://y.test/robots.txt", robots_ok()).add("https://y.test/jobs", reply(200, "on y"))
    result = make(transport)[0].fetch(PAGE)
    assert result.status == "ok" and result.final_url == "https://y.test/jobs"
    assert transport.calls == [ROBOTS, PAGE, "https://y.test/robots.txt", "https://y.test/jobs"]


def test_a_redirect_into_a_disallowed_path_is_blocked_by_that_hosts_robots():
    transport = simple(redirect("https://y.test/jobs"))
    transport.add("https://y.test/robots.txt", robots_ok("User-agent: *\nDisallow: /"))
    result = make(transport)[0].fetch(PAGE)
    assert result.status == "blocked" and result.block.reason == "robots_disallow"
    assert result.block.url == "https://y.test/jobs" and result.redirect_chain == ("https://y.test/jobs",)
    assert "https://y.test/jobs" not in transport.calls


def _chain(transport, count):
    for number in range(count):
        transport.add(f"https://x.test/r{number}", redirect(f"/r{number + 1}"))
    transport.add(f"https://x.test/r{count}", reply(200, "end"))


def test_five_redirects_are_followed():
    transport = FakeTransport().add(ROBOTS, robots_ok())
    _chain(transport, 5)
    result = make(transport)[0].fetch("https://x.test/r0")
    assert result.status == "ok" and len(result.redirect_chain) == 5


def test_a_sixth_redirect_is_refused():
    transport = FakeTransport().add(ROBOTS, robots_ok())
    _chain(transport, 6)
    result = make(transport)[0].fetch("https://x.test/r0")
    assert result.error.kind == "redirect_loop" and "more than 5" in result.error.message
    assert "https://x.test/r6" not in transport.calls


def test_a_redirect_loop_is_detected():
    transport = FakeTransport().add(ROBOTS, robots_ok())
    transport.add("https://x.test/a", redirect("/b")).add("https://x.test/b", redirect("/a"))
    result = make(transport)[0].fetch("https://x.test/a")
    assert result.error.kind == "redirect_loop" and "already visited" in result.error.message


def test_a_redirect_without_a_location_is_reported():
    result = make(simple(reply(302, "", ctype="")))[0].fetch(PAGE)
    assert result.error.kind == "redirect_loop" and "Location" in result.error.message


def test_a_redirect_to_a_non_http_address_is_invalid_url():
    result = make(simple(redirect("ftp://x.test/file")))[0].fetch(PAGE)
    assert result.error.kind == "invalid_url" and "redirect target" in result.error.message


# -- the body -------------------------------------------------------------------------------------------------

def test_a_gzip_body_is_decoded():
    page = reply(200, gzip.compress("héllo".encode()), Content_Encoding="gzip")
    assert make(simple(page))[0].fetch(PAGE).text == "héllo"


def test_the_charset_fallback_is_recorded_on_the_result():
    result = make(simple(reply(200, b"caf\xe9", ctype="text/html")))[0].fetch(PAGE)
    assert result.text == "café"
    assert (result.charset_used, result.charset_source) == ("cp1252", "fallback-cp1252")
    assert "cp1252" in result.charset_note and result.content_type == "text/html"


def test_an_empty_204_is_ok_with_empty_text():
    result = make(simple(reply(204, "", ctype="")))[0].fetch(PAGE)
    assert result.status == "ok" and result.text == ""


def test_a_pdf_is_not_text():
    result = make(simple(reply(200, b"%PDF", ctype="application/pdf")))[0].fetch(PAGE)
    assert result.error.kind == "not_text" and result.http_status == 200


def test_a_truncated_body_is_too_large():
    page = RawResponse(200, {"content-type": "text/html"}, b"x" * 10, True)
    assert make(simple(page))[0].fetch(PAGE).error.kind == "too_large"


def test_a_corrupt_gzip_body_is_a_decode_error():
    page = reply(200, b"not gzip", Content_Encoding="gzip")
    assert make(simple(page))[0].fetch(PAGE).error.kind == "decode"


# -- nothing raises ----------------------------------------------------------------------------------------------

def test_a_bug_in_the_transport_becomes_failed_internal():
    transport = simple(KeyError("oops"))
    result = make(transport)[0].fetch(PAGE)
    assert result.status == "failed" and result.error.kind == "internal"
    assert "KeyError" in result.error.message and result.requests == (ROBOTS, PAGE)


def test_a_replay_miss_is_failed_internal_and_says_not_in_cache():
    result = make(simple(CacheMiss("replay: x is not in the cache")))[0].fetch(PAGE)
    assert result.error.kind == "internal" and "not in the cache" in result.error.message


def test_the_test_alarms_are_not_swallowed():
    with pytest.raises(NetworkAttempt):
        make(simple(NetworkAttempt("real network")))[0].fetch(PAGE)
    with pytest.raises(UnscriptedRequest):
        make(FakeTransport())[0].fetch(PAGE)


class RandomTransport:
    """Random answers of every kind: whatever happens, fetch() must return a valid result."""

    def __init__(self, seed):
        self.rng = random.Random(seed)

    def __call__(self, url, headers, timeout):
        roll = self.rng.random()
        if roll < 0.2:
            raise TransportError(self.rng.choice(["timeout", "dns", "connection", "tls"]), "boom")
        status = self.rng.choice([200, 200, 204, 301, 302, 400, 401, 403, 404, 429, 500, 503, 999])
        headers = {"content-type": self.rng.choice(["text/html", "application/pdf", "text/plain", ""])}
        if 300 <= status < 400 and self.rng.random() < 0.8:
            headers["location"] = self.rng.choice(["/a", "/b", "https://z.test/", "ftp://x/y", ""])
        body = bytes(self.rng.randrange(256) for _ in range(self.rng.randrange(0, 40)))
        return RawResponse(status, headers, body, self.rng.random() < 0.05)


def test_random_answers_always_give_a_valid_result_and_never_raise():
    fetcher = Fetcher(RandomTransport(3), UA, sleep=lambda s: None)
    for number in range(300):
        url = f"https://{random.Random(number).choice(['a', 'b', 'c', 'd'])}.test/p{number % 7}"
        result = fetcher.fetch(url)
        assert isinstance(result, FetchResult)
        assert result.status in ("ok", "failed", "blocked")
        if result.error:
            assert result.error.kind in ERROR_KINDS


# -- record and replay together ------------------------------------------------------------------------------------

def test_a_recorded_run_replays_identically_without_any_transport(tmp_path):
    cache = ResponseCache(tmp_path / "triage_cache")
    live, _ = make(RecordingTransport(simple(), cache, clock=lambda: "t"))
    first = live.fetch(PAGE)

    replay = Fetcher(ReplayTransport(cache), UA, sleep=lambda s: None)
    second = replay.fetch(PAGE)
    assert second == first and second.status == "ok"


def test_a_recorded_failure_replays_as_the_same_failure(tmp_path):
    cache = ResponseCache(tmp_path / "c")
    transport = simple(robots=TransportError("tls", "certificate verify failed"))
    first = make(RecordingTransport(transport, cache, clock=lambda: "t"))[0].fetch(PAGE)
    second = Fetcher(ReplayTransport(cache), UA, sleep=lambda s: None).fetch(PAGE)
    assert first.error.kind == second.error.kind == "tls" and second == first


def test_replay_of_a_page_that_was_never_recorded_is_failed_internal(tmp_path):
    cache = ResponseCache(tmp_path / "c")
    make(RecordingTransport(simple(), cache, clock=lambda: "t"))[0].fetch(PAGE)
    result = Fetcher(ReplayTransport(cache), UA, sleep=lambda s: None).fetch(OTHER)
    assert result.error.kind == "internal" and "not in the cache" in result.error.message


def test_a_broken_cache_file_is_failed_internal_not_a_crash(tmp_path):
    cache = ResponseCache(tmp_path / "c")
    path = cache.path_for(ROBOTS)
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    result = Fetcher(ReplayTransport(cache), UA, sleep=lambda s: None).fetch(PAGE)
    assert result.status == "failed" and result.error.kind == "internal"
    assert "not valid" in result.error.message


# -- the FetchResult type itself ------------------------------------------------------------------------------------------

ERR = TriageError.of("timeout", "slow", PAGE)
BLK = BlockInfo("http_403", PAGE, 403)


def test_fetch_result_invariants():
    FetchResult(PAGE, PAGE, "ok", text="x")
    FetchResult(PAGE, PAGE, "failed", error=ERR)
    FetchResult(PAGE, PAGE, "blocked", block=BLK)
    bad = [
        dict(status="ok"), dict(status="ok", text="x", error=ERR), dict(status="ok", text="x", block=BLK),
        dict(status="failed"), dict(status="failed", error=ERR, text="x"), dict(status="failed", error=ERR, block=BLK),
        dict(status="blocked"), dict(status="blocked", block=BLK, error=ERR), dict(status="blocked", block=BLK, text="x"),
        dict(status="maybe"),
    ]
    for fields in bad:
        with pytest.raises(ContractError):
            FetchResult(PAGE, PAGE, **fields)


def test_fetch_result_wants_tuples():
    with pytest.raises(ContractError):
        FetchResult(PAGE, PAGE, "ok", text="x", requests=[PAGE])


# -- broken redirect addresses ------------------------------------------------------------------------------------------

def test_a_redirect_to_a_broken_address_is_invalid_url_not_internal():
    transport = simple(redirect("http://[bad"))
    result = make(transport)[0].fetch(PAGE)
    assert result.status == "failed" and result.error.kind == "invalid_url"
    assert "redirect target" in result.error.message


def test_a_robots_redirect_to_a_broken_address_is_reported_not_fatal():
    transport = simple(robots=redirect("http://[bad"))
    result = make(transport)[0].fetch(PAGE)
    assert result.status == "failed" and result.error.kind == "redirect_loop"
    assert "unusable redirect" in result.error.message


# -- allow_truncated: keep the first 2 MB of a big body (for sitemaps) ---------------------------------------------------

def test_a_body_cut_at_the_cap_is_a_failure_by_default_and_the_start_of_the_text_when_allowed():
    cut = RawResponse(200, {"content-type": "text/html"}, b"<p>first part of a very big page", True)
    transport = simple(cut)
    default = make(transport)[0].fetch(PAGE)
    assert default.error.kind == "too_large" and default.truncated is False
    allowed = make(simple(cut))[0].fetch(PAGE, allow_truncated=True)
    assert allowed.status == "ok" and allowed.truncated is True
    assert allowed.text == "<p>first part of a very big page" and allowed.http_status == 200


def test_a_character_cut_in_two_by_the_cap_still_decodes_as_utf8():
    body = ("é" * 10).encode("utf-8")[:-1]
    cut = RawResponse(200, {"content-type": "text/html"}, body, True)
    result = make(simple(cut))[0].fetch(PAGE, allow_truncated=True)
    assert result.text == "é" * 9 and result.charset_source == "default-utf8"


def test_a_compressed_body_cut_at_the_cap_gives_the_text_so_far():
    text = "".join(f"line number {n}\n" for n in range(5000))
    wire = gzip.compress(text.encode())
    cut = RawResponse(200, {"content-type": "text/plain", "content-encoding": "gzip"}, wire[: len(wire) // 2], True)
    result = make(simple(cut))[0].fetch(PAGE, allow_truncated=True)
    assert result.status == "ok" and result.truncated is True and result.text.startswith("line number 0\n")
    assert make(simple(cut))[0].fetch(PAGE).error.kind == "too_large"


def test_a_small_compressed_body_that_expands_past_the_cap_is_cut_to_the_cap_when_allowed():
    big = gzip.compress(b"a" * (3 * 1024 * 1024))
    page = RawResponse(200, {"content-type": "text/plain", "content-encoding": "gzip"}, big, False)
    default = make(simple(page))[0].fetch(PAGE)
    assert default.error.kind == "too_large"
    allowed = make(simple(page))[0].fetch(PAGE, allow_truncated=True)
    assert allowed.status == "ok" and allowed.truncated is True and len(allowed.text) == 2 * 1024 * 1024


def test_a_normal_page_is_not_truncated_even_when_truncation_is_allowed():
    result = make(simple())[0].fetch(PAGE, allow_truncated=True)
    assert result.status == "ok" and result.truncated is False


def test_robots_txt_is_never_read_in_part():
    cut = RawResponse(200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow:", True)
    result = make(simple(robots=cut))[0].fetch(PAGE, allow_truncated=True)
    assert result.status == "failed" and result.error.kind == "too_large" and "robots" in result.error.message


def test_other_failures_are_unchanged_when_truncation_is_allowed():
    assert make(simple(reply(404)))[0].fetch(PAGE, allow_truncated=True).error.kind == "http_4xx"
    assert make(simple(reply(403)))[0].fetch(PAGE, allow_truncated=True).status == "blocked"


def test_only_an_ok_fetch_can_be_marked_truncated():
    with pytest.raises(ContractError):
        FetchResult(PAGE, PAGE, "failed", error=ERR, truncated=True)
    with pytest.raises(ContractError):
        FetchResult(PAGE, PAGE, "blocked", block=BLK, truncated=True)
    assert FetchResult(PAGE, PAGE, "ok", text="x", truncated=True).truncated is True


# -- a sitemap cut at the cap flows through the fetcher into the sitemap reader --------------------------------------------

SITEMAP_URL = "https://x.test/sitemap.xml"


def cut_sitemap_answer() -> RawResponse:
    entries = "".join(f"<url><loc>https://x.test/jobs/view/{n}</loc></url>" for n in range(10))
    text = f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'
    cut = text[: text.index("/jobs/view/9") + 5]                      # ends inside the last <loc>
    return RawResponse(200, {"content-type": "application/xml"}, cut.encode(), True)


def test_a_sitemap_cut_at_the_cap_is_read_as_far_as_it_goes():
    transport = FakeTransport().add(ROBOTS, robots_ok()).add(SITEMAP_URL, cut_sitemap_answer())
    fetcher, _ = make(transport)
    result = fetcher.fetch(SITEMAP_URL, allow_truncated=True)
    assert result.status == "ok" and result.truncated is True
    found = read_sitemap(result.final_url, result.text, "https://x.test/jobs/view/100")
    assert len(found.urls) == 9 and found.job_url_count == 9 and found.evidence[0].kind == "sitemap_jobs"


def test_the_same_cut_sitemap_is_a_failure_when_truncation_is_not_allowed():
    transport = FakeTransport().add(ROBOTS, robots_ok()).add(SITEMAP_URL, cut_sitemap_answer())
    assert make(transport)[0].fetch(SITEMAP_URL).error.kind == "too_large"


def test_a_cut_robots_file_stays_a_failure_even_when_the_page_fetch_allows_truncation():
    cut_robots = RawResponse(200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /pri", True)
    transport = FakeTransport().add(ROBOTS, cut_robots).add(PAGE, reply(200, "page"))
    result = make(transport)[0].fetch(PAGE, allow_truncated=True)
    assert result.status == "failed" and result.error.kind == "too_large" and "robots.txt" in result.error.message


def test_a_truncated_fetch_replays_identically_from_the_cache(tmp_path):
    cache = ResponseCache(tmp_path / "c")
    transport = FakeTransport().add(ROBOTS, robots_ok()).add(SITEMAP_URL, cut_sitemap_answer())
    first = make(RecordingTransport(transport, cache, clock=lambda: "t"))[0].fetch(SITEMAP_URL, allow_truncated=True)
    second = Fetcher(ReplayTransport(cache), UA, sleep=lambda s: None).fetch(SITEMAP_URL, allow_truncated=True)
    assert second == first and second.truncated is True
