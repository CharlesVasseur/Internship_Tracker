"""Two rules found by the first live run (T6.5), checked offline on a fake transport.

1. An address with the default port (https :443, http :80) is the same site as one
   without a port. A redirect to "https://host:443/..." must not fetch robots.txt again.
2. A Crawl-delay is a wish of a host, not of one origin. Once any origin of a host has
   asked for N seconds, every later request to that host waits N seconds, including the
   robots.txt request of another origin (http against https) of the same host.

Real case behind both: a careers site answered a Sitemap line written with http://, then
redirected to https://host:443/. The old code fetched robots.txt three extra times, two of
them 0 to 2 seconds after the previous request although the site asked for 5 seconds.
All hosts here are example.test: no real firm appears in the repository.
"""

from tests.fake_transport import (
    ALLOW_ALL,
    FakeClock,
    FakeTransport,
    redirect,
    reply,
    robots_ok,
)
from tracker.triage.fetcher import Fetcher

AGENT = "InternshipTracker/0.1 (triage; contact test@example.test)"
PAGE = "<html><body>hello</body></html>"
SLOW = "User-agent: *\nDisallow:\nCrawl-delay: 5"


def make(transport_clock=True):
    clock = FakeClock()
    transport = FakeTransport(clock=clock if transport_clock else None)
    fetcher = Fetcher(transport, AGENT, sleep=clock.sleep, clock=clock)
    return fetcher, transport, clock


def robots_requests(transport):
    return [url for url in transport.calls if url.endswith("/robots.txt")]


# -- rule 1: the default port is not a different site ---------------------------------


def test_https_default_port_is_the_same_origin():
    fetcher, transport, _ = make()
    transport.add("https://example.test/robots.txt", robots_ok())
    transport.add("https://example.test/a", reply(200, PAGE))
    transport.add("https://example.test:443/b", reply(200, PAGE))

    assert fetcher.fetch("https://example.test/a").status == "ok"
    assert fetcher.fetch("https://example.test:443/b").status == "ok"

    assert robots_requests(transport) == ["https://example.test/robots.txt"]


def test_http_default_port_is_the_same_origin():
    fetcher, transport, _ = make()
    transport.add("http://example.test/robots.txt", robots_ok())
    transport.add("http://example.test:80/a", reply(200, PAGE))
    transport.add("http://example.test/b", reply(200, PAGE))

    assert fetcher.fetch("http://example.test:80/a").status == "ok"
    assert fetcher.fetch("http://example.test/b").status == "ok"

    assert robots_requests(transport) == ["http://example.test/robots.txt"]


def test_a_redirect_to_the_explicit_default_port_does_not_refetch_robots():
    fetcher, transport, _ = make()
    transport.add("https://example.test/robots.txt", robots_ok())
    transport.add("https://example.test/a", redirect("https://example.test:443/b"))
    transport.add("https://example.test:443/b", reply(200, PAGE))

    result = fetcher.fetch("https://example.test/a")

    assert result.status == "ok"
    assert robots_requests(transport) == ["https://example.test/robots.txt"]


def test_a_port_that_is_not_the_default_is_still_another_origin():
    fetcher, transport, _ = make()
    transport.add("https://example.test/robots.txt", robots_ok())
    transport.add("https://example.test:8443/robots.txt", robots_ok())
    transport.add("https://example.test/a", reply(200, PAGE))
    transport.add("https://example.test:8443/b", reply(200, PAGE))

    fetcher.fetch("https://example.test/a")
    fetcher.fetch("https://example.test:8443/b")

    assert robots_requests(transport) == [
        "https://example.test/robots.txt",
        "https://example.test:8443/robots.txt",
    ]


def test_http_and_https_stay_different_origins():
    fetcher, transport, _ = make()
    transport.add("https://example.test/robots.txt", robots_ok())
    transport.add("http://example.test/robots.txt", robots_ok())
    transport.add("https://example.test/a", reply(200, PAGE))
    transport.add("http://example.test/b", reply(200, PAGE))

    fetcher.fetch("https://example.test/a")
    fetcher.fetch("http://example.test/b")

    assert robots_requests(transport) == [
        "https://example.test/robots.txt",
        "http://example.test/robots.txt",
    ]


# -- rule 2: a Crawl-delay belongs to the host, whatever the origin -------------------


def test_crawl_delay_also_applies_to_robots_txt_of_another_origin_of_the_host():
    fetcher, transport, _ = make()
    transport.add("https://example.test/robots.txt", robots_ok(SLOW))
    transport.add("http://example.test/robots.txt", robots_ok(ALLOW_ALL))   # says nothing about a delay
    transport.add("https://example.test/a", reply(200, PAGE))
    transport.add("http://example.test/b", reply(200, PAGE))

    fetcher.fetch("https://example.test/a")
    fetcher.fetch("http://example.test/b")

    # robots https, page https, robots http, page http
    assert len(transport.calls) == 4
    gaps = [later - earlier for earlier, later in zip(transport.times, transport.times[1:])]
    assert all(gap >= 5.0 for gap in gaps), gaps


def test_the_delay_learned_on_one_origin_holds_for_a_redirect_to_the_other_origin():
    # The real sequence: a Sitemap line with http://, then a redirect to https://host:443/.
    fetcher, transport, _ = make()
    transport.add("https://example.test/robots.txt", robots_ok(SLOW))
    transport.add("http://example.test/robots.txt", robots_ok(SLOW))
    transport.add("https://example.test/jobs", reply(200, PAGE))
    transport.add("http://example.test/sitemap.xml", redirect("https://example.test:443/sitemap.xml"))
    transport.add("https://example.test:443/sitemap.xml", reply(200, PAGE))

    fetcher.fetch("https://example.test/jobs")
    result = fetcher.fetch("http://example.test/sitemap.xml")

    assert result.status == "ok"
    # robots https, jobs, robots http, sitemap http (301), sitemap https: no second robots.txt
    assert robots_requests(transport) == [
        "https://example.test/robots.txt",
        "http://example.test/robots.txt",
    ]
    gaps = [later - earlier for earlier, later in zip(transport.times, transport.times[1:])]
    assert all(gap >= 5.0 for gap in gaps), gaps


def test_the_delay_of_one_host_does_not_slow_down_another_host():
    fetcher, transport, _ = make()
    transport.add("https://example.test/robots.txt", robots_ok(SLOW))
    transport.add("https://example.test/a", reply(200, PAGE))
    transport.add("https://other.example.test/robots.txt", robots_ok(ALLOW_ALL))
    transport.add("https://other.example.test/b", reply(200, PAGE))

    fetcher.fetch("https://example.test/a")
    start = len(transport.calls)
    fetcher.fetch("https://other.example.test/b")

    other_times = transport.times[start:]
    assert len(other_times) == 2                      # its robots.txt and its page
    assert other_times[1] - other_times[0] == 1.0     # only the normal 1 second
