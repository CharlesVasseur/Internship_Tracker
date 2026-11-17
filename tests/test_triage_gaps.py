"""Gap tests from the T5.1 matrix (T5.4): budgets, unusable addresses, defensive branches, the CLI's
warnings, contract validation, JSON that is not a result, and random input on the whole of triage_firm.

Offline: fake transport, fake clock, tmp files. Every page and address is invented.
"""

import json
import random
from types import SimpleNamespace

import pytest

from fake_transport import ALLOW_ALL, FakeClock, FakeTransport, UnscriptedRequest, reply, robots_ok
from tracker.triage.cli import main
from tracker.triage.contract import (MAX_MESSAGE, MAX_QUOTE, STATUSES, ContractError, Evidence, FirmInput,
                                     TermsFlag, TriageError, TriageResult)
from tracker.triage.fetcher import Fetcher
from tracker.triage.orchestrator import triage_firm
from tracker.triage.transport import RawResponse, TransportError

H = "https://careers.example.test"
CAREERS, LISTING, EXAMPLE, TERMS = H + "/careers", H + "/jobs", H + "/jobs/1", H + "/terms"
AGENT = "InternshipTracker/0.1 (triage; contact test@example.test)"
PLAIN = "<html><body><p>Open roles are listed on our recruiting page.</p><a href='/about'>About</a></body></html>"
RSS = 'application/rss+xml'


def page_with_feed(href):
    return f'<html><head><link rel="alternate" type="{RSS}" href="{href}"></head><body><p>Roles are online.</p></body></html>'


class Site:
    def __init__(self, transport_class=FakeTransport):
        self.clock = FakeClock()
        self.transport = transport_class(self.clock)
        self.fetcher = Fetcher(self.transport, AGENT, sleep=self.clock.sleep, clock=self.clock)

    def robots(self, host=H, text=ALLOW_ALL):
        self.transport.add(host + "/robots.txt", robots_ok(text))
        return self

    def run(self, firm, **options):
        return triage_firm(firm, self.fetcher, clock=self.clock, **options)


def firm(**changes):
    values = dict(name="Example Firm", careers_url=CAREERS, listing_url=LISTING, example_job_url=EXAMPLE)
    values.update(changes)
    return FirmInput(**values)


def notes(result):
    return " ".join(item.detail for item in result.evidence if item.kind == "note")


# -- G1: the default cap of 10 requests is really reached -------------------------------------------------------------


def test_the_default_cap_of_ten_requests_is_reached_and_the_terms_page_is_skipped_with_a_reason():
    jobs, feeds, maps, more, legal = (f"https://{name}.example.test" for name in ("jobs", "feeds", "maps", "more", "legal"))
    s = Site().robots(H, f"{ALLOW_ALL}\nSitemap: {maps}/sitemap.xml\n")
    for host in (jobs, feeds, maps, more, legal):
        s.robots(host)
    s.transport.add(LISTING, reply(200, page_with_feed(feeds + "/feed.xml")))
    s.transport.add(jobs + "/jobs/1", reply(200, "<html><body>Intern</body></html>"))
    s.transport.add(feeds + "/feed.xml", reply(200, "<rss><channel><title>Jobs</title></channel></rss>", ctype="application/xml"))
    s.transport.add(maps + "/sitemap.xml", reply(200, f"<sitemapindex><sitemap><loc>{more}/s1.xml</loc></sitemap></sitemapindex>", ctype="application/xml"))
    s.transport.add(more + "/s1.xml", reply(200, "<urlset></urlset>", ctype="application/xml"))
    s.transport.add(legal + "/terms", reply(200, "<html><body>Terms</body></html>"))
    result = s.run(firm(example_job_url=jobs + "/jobs/1", terms_url=legal + "/terms"))
    assert len(result.requests) == 10                         # robots + page on five hosts, no more
    assert result.requests[-1] == more + "/s1.xml" and legal + "/terms" not in result.requests
    assert result.terms.status == "not_assessed" and "request budget used up (10 of 10)" in result.terms.why
    assert (result.status, result.mode) == ("ok", "manual")   # every step that ran completed


# -- G2 and G3: a feed or a sitemap skipped for budget makes manual unresolved ---------------------------------------------


def test_a_feed_skipped_for_budget_makes_a_manual_firm_unresolved():
    s = Site().robots()
    s.transport.add(LISTING, reply(200, page_with_feed(H + "/feed.xml"))).add(EXAMPLE, reply(200, PLAIN))
    result = s.run(firm(), max_requests=3)                     # robots, listing, job page: the cap is reached
    assert (result.status, result.error.kind, result.error.retryable) == ("failed", "budget", True)
    assert "feed not fetched: request budget used up (3 of 3)" in result.error.message
    assert H + "/feed.xml" not in result.requests


def test_a_sitemap_skipped_for_budget_makes_a_manual_firm_unresolved():
    s = Site().robots(H, f"{ALLOW_ALL}\nSitemap: {H}/sitemap.xml\n")
    s.transport.add(LISTING, reply(200, PLAIN)).add(EXAMPLE, reply(200, PLAIN))
    result = s.run(firm(), max_requests=3)
    assert (result.status, result.error.kind) == ("failed", "budget")
    assert "sitemap not fetched" in result.error.message and H + "/sitemap.xml" not in result.requests


# -- G4, G5, G6: addresses that cannot be used --------------------------------------------------------------------------------


def test_a_feed_link_that_is_not_a_usable_address_is_a_note_and_is_never_requested():
    s = Site().robots()
    s.transport.add(LISTING, reply(200, page_with_feed("http://[bad"))).add(EXAMPLE, reply(200, PLAIN))
    result = s.run(firm())
    assert (result.status, result.mode) == ("ok", "manual")
    assert "feed link is not a usable address" in notes(result)
    assert result.requests == (H + "/robots.txt", LISTING, EXAMPLE)


def test_a_sitemap_line_that_is_not_a_usable_address_is_a_note_and_is_never_requested():
    s = Site().robots(H, f"{ALLOW_ALL}\nSitemap: http://[bad\n")
    s.transport.add(LISTING, reply(200, PLAIN)).add(EXAMPLE, reply(200, PLAIN))
    result = s.run(firm())
    assert (result.status, result.mode) == ("ok", "manual")
    assert "Sitemap line is not a usable address" in notes(result)
    assert "sitemap_line" in [item.kind for item in result.evidence]


@pytest.mark.parametrize("address", ["http://[bad", "http://[::1", "https://x.test:99999/a", "ftp://x.test/a"])
def test_a_malformed_address_never_raises_even_in_the_linkedin_check(address):
    result = Site().run(firm(careers_url=address, listing_url=None, example_job_url=None))
    assert (result.status, result.error.kind) == ("failed", "invalid_url")
    assert result.requests == ()


# -- G7: two defensive branches ---------------------------------------------------------------------------------------------


def test_robots_rules_missing_after_an_ok_page_is_a_visible_note():
    s = Site().robots()
    s.transport.add(LISTING, reply(200, PLAIN)).add(EXAMPLE, reply(200, PLAIN))
    s.fetcher.robots_rules = lambda url: None                  # the situation the code defends against
    result = s.run(firm())
    assert (result.status, result.mode) == ("ok", "manual")
    assert "robots.txt rules are not available" in notes(result)


def test_a_secondary_fetch_with_no_usable_answer_is_an_internal_failure_not_a_manual_firm():
    s = Site().robots()
    s.transport.add(LISTING, reply(200, PLAIN))
    real_fetch = s.fetcher.fetch

    def fetch(url, **kw):
        if url == EXAMPLE:                                      # neither an error nor a block: should never happen
            return SimpleNamespace(ok=False, status="failed", block=None, error=None, url=EXAMPLE, final_url=EXAMPLE)
        return real_fetch(url, **kw)

    s.fetcher.fetch = fetch
    result = s.run(firm())
    assert (result.status, result.error.kind) == ("failed", "internal")
    assert "no usable answer" in result.error.message


# -- G8: the CLI shows the runner's warnings ------------------------------------------------------------------------------------


def run_cli(tmp_path, rows, *extra, transport=None):
    csv_path = tmp_path / "firms.csv"
    csv_path.write_text("firm;careers_entry_url;listing_url;example_job_url;terms_url\n" + "\n".join(rows) + "\n", encoding="utf-8")
    clock = FakeClock()
    return main(["--input", str(csv_path), "--output", str(tmp_path / "out.jsonl"), "--cache-dir", str(tmp_path / "cache"), *extra],
                environ={"SURVEY_CONTACT": "t@example.test"}, live_transport=transport or FakeTransport(clock),
                sleep=clock.sleep, clock=clock)


def cli_site():
    transport = FakeTransport()
    transport.add(H + "/robots.txt", robots_ok(ALLOW_ALL)).add(CAREERS, reply(200, PLAIN))
    return transport


def test_the_cli_shows_the_runners_warning_for_a_repeated_firm(tmp_path, capsys):
    row = f"Example Firm;{CAREERS};;;"
    assert run_cli(tmp_path, [row, row], transport=cli_site()) == 0
    err = capsys.readouterr().err
    assert "run once" in err and "appears more than once" in err      # the reader's warning and the runner's


def test_the_cli_shows_a_warning_for_an_unreadable_line_on_resume(tmp_path, capsys):
    row = f"Example Firm;{CAREERS};;;"
    run_cli(tmp_path, [row], transport=cli_site())
    with (tmp_path / "out.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("this is not a result\n")
    capsys.readouterr()
    assert run_cli(tmp_path, [row], "--resume") == 0
    assert "out.jsonl line 2: not a readable result, ignored" in capsys.readouterr().err


# -- G9: contract validation that no test reached ---------------------------------------------------------------------------------


def test_an_unknown_error_kind_is_refused():
    with pytest.raises(ContractError, match="unknown error kind"):
        TriageError("nonsense", "x", None, False)


def test_a_too_long_error_message_is_refused():
    with pytest.raises(ContractError, match="characters"):
        TriageError("internal", "x" * (MAX_MESSAGE + 1), None, False)


def test_a_too_long_terms_quote_is_refused():
    with pytest.raises(ContractError, match="quote is longer"):
        TermsFlag("https://a.example.test", "flagged", quotes=("q" * (MAX_QUOTE + 1),))


def test_requests_must_be_a_tuple():
    with pytest.raises(ContractError, match="tuple of URLs"):
        TriageResult(firm=FirmInput("Example Firm", "https://a.example.test"), status="ok", mode="manual",
                     checked_at="2026-11-16T10:00:00+00:00", evidence=(Evidence("note", "https://a.example.test", "n"),),
                     requests=["https://a.example.test"])


# -- G10: JSON that is not a result is a ContractError, never another exception ------------------------------------------------------


@pytest.mark.parametrize("line", ["[]", "5", '"text"', "null", "true", "[1, 2]", "{}", '{"firm": []}', '{"firm": 5}', "not json", ""])
def test_json_that_is_not_a_result_raises_a_contract_error(line):
    with pytest.raises(ContractError):
        TriageResult.from_json(line)


@pytest.mark.parametrize("data", [[], 5, "text", None, True])
def test_from_dict_refuses_data_that_is_not_a_dict(data):
    with pytest.raises(ContractError):
        TriageResult.from_dict(data)


# -- G11: random pages and answers never crash triage_firm ------------------------------------------------------------------------------


class LenientTransport(FakeTransport):
    """An address nobody scripted is a connection error, so random links cannot stop the test."""

    def __call__(self, *args, **kwargs):
        try:
            return super().__call__(*args, **kwargs)
        except UnscriptedRequest:
            raise TransportError("connection", "not scripted") from None


FRAGMENTS = [
    "<a href='/jobs/{n}'>Job</a>", "<div id=\"root\"></div>", "<p>Some text about the roles.</p>", "<!DOCTYPE html>",
    '<script type="application/ld+json">{"@type":"JobPosting","title":"T"}</script>',
    '<script type="application/ld+json">{broken json</script>', '<script type="application/ld+json">' + "[" * 3000 + "</script>",
    '<link rel="alternate" type="application/rss+xml" href="{feed}">', "<noscript>Enable JavaScript</noscript>",
    '<a href="https://acme.myworkdayjobs.com/x">Apply</a>', "<a href='http://[bad'>x</a>", "<a href='javascript:void(0)'>x</a>",
    "<form action='/search'>", "é\u00a0ü ñ", "\x00\x01", "<", ">>>", "&amp;&lt;", "<p>" * 50,
]
ROBOTS_BODIES = ["", ALLOW_ALL, "User-agent: *\nDisallow: /jobs\n", "User-agent: *\nCrawl-delay: 2\n", "User-agent: *\nCrawl-delay: 99\n",
                 f"{ALLOW_ALL}\nSitemap: {H}/sitemap.xml\n", f"{ALLOW_ALL}\nSitemap: http://[bad\n", "<html>not robots</html>", "\xe9\xff"]


def random_body(rng, feed):
    parts = [rng.choice(FRAGMENTS).replace("{n}", str(rng.randrange(1, 9))).replace("{feed}", feed) for _ in range(rng.randrange(0, 25))]
    if rng.random() < 0.15:
        parts.append("x" * rng.randrange(1000, 60000))
    text = "".join(parts)
    return text.encode("utf-8") if rng.random() < 0.85 else text.encode("utf-8") + b"\xff\xfe\x80"


def random_answer(rng, urls, feed):
    roll = rng.random()
    if roll < 0.12:
        return TransportError(rng.choice(["timeout", "dns", "connection", "tls"]), "scripted")
    if roll < 0.20:
        return RawResponse(rng.choice([403, 404, 429, 500, 503, 401, 418, 999]), {"content-type": "text/html"}, b"no", False)
    if roll < 0.26:
        return RawResponse(301, {"location": rng.choice(urls + ["http://[bad", "/relative"])}, b"", False)
    kind = rng.choice(["text/html", "text/html", "application/xml", "application/pdf", "application/json", "", "text/html; charset=nonsense"])
    return RawResponse(200, {"content-type": kind}, random_body(rng, feed), rng.random() < 0.05)


def test_random_pages_and_answers_never_crash_triage_and_always_give_a_valid_result():
    rng = random.Random(20261116)
    urls = [LISTING, EXAMPLE, TERMS, H + "/feed.xml", H + "/sitemap.xml"]
    seen = set()
    for round_number in range(250):
        s = Site(LenientTransport)
        s.transport.add(H + "/robots.txt", robots_ok(rng.choice(ROBOTS_BODIES)))
        for url in urls:
            s.transport.add(url, random_answer(rng, urls, H + "/feed.xml"))
        candidate = firm(terms_url=TERMS, listing_url=rng.choice([LISTING, None]), example_job_url=rng.choice([EXAMPLE, None, LISTING]))
        result = s.run(candidate, max_requests=rng.choice([2, 4, 10]))
        context = f"round {round_number}"
        assert isinstance(result, TriageResult), context
        assert result.status in STATUSES and result.status != "empty", context
        assert TriageResult.from_json(result.to_json()) == result, context
        assert result.terms is not None, context
        seen.add((result.status, result.mode))
    assert {"ok", "failed", "blocked"} <= {status for status, _ in seen}      # the random input really exercised the paths
    assert len(seen) >= 6
