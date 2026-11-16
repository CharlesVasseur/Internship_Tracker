"""Tests for triage_firm (T4.2). Fake transport and fake clock; synthetic pages on example.test."""

import pytest

from fake_transport import ALLOW_ALL, FakeClock, FakeTransport, redirect, reply, robots_ok
from tracker.triage.contract import ContractError, Evidence, FirmInput, TriageResult
from tracker.triage.fetcher import Fetcher
from tracker.triage.orchestrator import choose_mode, triage_firm
from tracker.triage.transport import RawResponse, TransportError

HOST = "https://careers.example.test"
ROBOTS = HOST + "/robots.txt"
CAREERS = HOST + "/careers"
LISTING = HOST + "/jobs"
EXAMPLE = HOST + "/jobs/1"
TERMS = HOST + "/terms"
SITEMAP = HOST + "/sitemap.xml"
AGENT = "InternshipTracker/0.1 (triage; contact test@example.test)"

JSONLD_PAGE = (
    '<html><body><p>Intern</p><script type="application/ld+json">'
    '{"@context":"https://schema.org","@type":"JobPosting","title":"Intern"}</script></body></html>'
)
PLAIN_PAGE = "<html><body><p>Open roles are listed on our recruiting page.</p><a href='/about'>About</a></body></html>"


def jobs_page(count=6, extra=""):
    links = "".join(f'<a href="/jobs/{n}">Job {n}</a>' for n in range(1, count + 1))
    return f"<html><body><p>Open roles</p><a href='/about'>About</a>{links}{extra}</body></html>"


def urlset(count=6):
    entries = "".join(f"<url><loc>{HOST}/jobs/{n}</loc></url>" for n in range(1, count + 1))
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'


def feed(count=6, base=HOST + "/jobs/"):
    items = "".join(f"<item><title>J{n}</title><link>{base}{n}</link></item>" for n in range(1, count + 1))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Jobs</title>{items}</channel></rss>'


def xml(body, **kw):
    return reply(200, body, ctype="application/xml", **kw)


def firm(**changes):
    values = dict(name="Alpha", careers_url=CAREERS, listing_url=LISTING, example_job_url=EXAMPLE, terms_url=None)
    values.update(changes)
    return FirmInput(**values)


class Setup:
    def __init__(self):
        self.clock = FakeClock()
        self.transport = FakeTransport(self.clock)
        self.fetcher = Fetcher(self.transport, AGENT, sleep=self.clock.sleep, clock=self.clock)

    def site(self, listing=None, job=PLAIN_PAGE, robots=ALLOW_ALL):
        """Script robots.txt, the listing page and the example job page (None leaves a page unscripted)."""
        self.transport.add(ROBOTS, robots_ok(robots))
        if listing is not None:
            self.transport.add(LISTING, reply(200, listing))
        if job is not None:
            self.transport.add(EXAMPLE, reply(200, job))
        return self

    def run(self, firm_input=None, **options):
        return triage_firm(firm_input or firm(), self.fetcher, clock=self.clock, **options)


def kinds(result):
    return [item.kind for item in result.evidence]


def details(result, kind):
    return [item.detail for item in result.evidence if item.kind == kind]


# -- choose_mode: the spec's priority ----------------------------------------------------------------


def ev(kind):
    return Evidence(kind, "https://x.test", "d")


@pytest.mark.parametrize("present, expected", [
    (["feed_parsed", "jsonld_jobposting", "vendor_host", "job_links", "spa_shell"], "feed"),
    (["jsonld_jobposting", "vendor_host", "job_links", "spa_shell"], "json-ld"),
    (["vendor_host", "job_links", "spa_shell"], "vendor"),
    (["job_links", "spa_shell"], "html"),
    (["spa_shell"], "js-only"),
    (["note", "robots", "feed_link", "sitemap_line"], "manual"),
    ([], "manual"),
])
def test_the_mode_follows_the_spec_priority(present, expected):
    assert choose_mode([ev(kind) for kind in present]) == expected


def test_a_feed_link_alone_is_only_a_lead_not_a_feed():
    assert choose_mode([ev("feed_link")]) == "manual"


# -- the modes, end to end ------------------------------------------------------------------------------


def test_job_links_give_html_and_probable():
    result = Setup().site(jobs_page()).run()
    assert (result.status, result.mode, result.verdict) == ("ok", "html", "probable")
    assert any("match" in text for text in details(result, "job_links"))
    assert result.requests == (ROBOTS, LISTING, EXAMPLE)


def test_json_ld_plus_job_links_is_automatic():
    result = Setup().site(jobs_page(), job=JSONLD_PAGE).run()
    assert (result.mode, result.verdict) == ("json-ld", "automatic")


def test_json_ld_without_a_url_source_is_only_probable():
    result = Setup().site(PLAIN_PAGE, job=JSONLD_PAGE).run()
    assert (result.mode, result.verdict) == ("json-ld", "probable")
    assert "robots.txt has no Sitemap line" in " ".join(details(result, "note"))


def test_json_ld_plus_a_sitemap_of_job_urls_is_automatic():
    s = Setup().site(PLAIN_PAGE, job=JSONLD_PAGE, robots=f"{ALLOW_ALL}\nSitemap: {SITEMAP}")
    s.transport.add(SITEMAP, xml(urlset()))
    result = s.run()
    assert (result.mode, result.verdict) == ("json-ld", "automatic")
    assert "sitemap_jobs" in kinds(result) and "sitemap_line" in kinds(result)


def test_a_sitemap_index_reads_only_its_first_child():
    index = (f"<sitemapindex><sitemap><loc>{HOST}/s1.xml</loc></sitemap>"
             f"<sitemap><loc>{HOST}/s2.xml</loc></sitemap></sitemapindex>")
    s = Setup().site(PLAIN_PAGE, job=JSONLD_PAGE, robots=f"{ALLOW_ALL}\nSitemap: {SITEMAP}")
    s.transport.add(SITEMAP, xml(index)).add(HOST + "/s1.xml", xml(urlset())).add(HOST + "/s2.xml", xml(urlset()))
    result = s.run()
    assert HOST + "/s1.xml" in result.requests and HOST + "/s2.xml" not in result.requests
    assert result.verdict == "automatic"


def test_a_cut_sitemap_is_read_as_far_as_it_goes_and_says_so():
    s = Setup().site(PLAIN_PAGE, job=JSONLD_PAGE, robots=f"{ALLOW_ALL}\nSitemap: {SITEMAP}")
    s.transport.add(SITEMAP, RawResponse(200, {"content-type": "application/xml"}, urlset().encode(), True))
    result = s.run()
    assert "larger than the size cap" in " ".join(details(result, "note"))
    assert result.verdict == "automatic"


def test_the_sitemap_is_skipped_when_job_links_were_already_found():
    s = Setup().site(jobs_page(), job=JSONLD_PAGE, robots=f"{ALLOW_ALL}\nSitemap: {SITEMAP}")
    result = s.run()
    assert SITEMAP not in result.requests
    assert "sitemap not fetched" in " ".join(details(result, "note"))


def test_a_blocked_sitemap_is_a_note_not_a_blocked_firm():
    s = Setup().site(PLAIN_PAGE, job=JSONLD_PAGE, robots=f"User-agent: *\nDisallow: /sitemap\nSitemap: {SITEMAP}")
    result = s.run()
    assert result.status == "ok" and result.mode == "json-ld"
    assert "sitemap not read: blocked (robots_disallow)" in " ".join(details(result, "note"))
    assert SITEMAP not in result.requests


def test_a_job_feed_gives_feed_and_automatic():
    page = PLAIN_PAGE.replace(
        "<body>", f'<head><link rel="alternate" type="application/rss+xml" href="{HOST}/feed.xml"></head><body>')
    s = Setup().site(page)
    s.transport.add(HOST + "/feed.xml", xml(feed()))
    result = s.run()
    assert (result.mode, result.verdict) == ("feed", "automatic")
    assert "feed_parsed" in kinds(result)


def test_a_blog_feed_stays_a_lead_and_the_job_links_decide():
    page = jobs_page().replace(
        "<body>", f'<head><link rel="alternate" type="application/rss+xml" href="{HOST}/blog.xml"></head><body>')
    s = Setup().site(page)
    s.transport.add(HOST + "/blog.xml", xml(feed(base=HOST + "/blog/")))
    result = s.run()
    assert (result.mode, result.verdict) == ("html", "probable")
    assert "feed_link" in kinds(result) and "feed_parsed" not in kinds(result)
    assert "not a job feed" in " ".join(details(result, "note"))


def test_only_the_first_of_several_feed_links_is_fetched():
    heads = "".join(
        f'<link rel="alternate" type="application/rss+xml" href="{HOST}/f{n}.xml">' for n in (1, 2, 3))
    s = Setup().site(PLAIN_PAGE.replace("<body>", f"<head>{heads}</head><body>"))
    s.transport.add(HOST + "/f1.xml", xml(feed(count=1)))
    result = s.run()
    assert HOST + "/f1.xml" in result.requests
    assert HOST + "/f2.xml" not in result.requests
    assert "2 more feed link(s)" in " ".join(details(result, "note"))


def test_a_recruiting_system_host_gives_vendor():
    page = PLAIN_PAGE.replace("About</a>", "About</a><a href='https://acme.myworkdayjobs.com/en/site'>Jobs</a>")
    result = Setup().site(page).run()
    assert (result.mode, result.verdict) == ("vendor", "probable")


def test_an_empty_app_shell_gives_js_only_and_manual():
    result = Setup().site('<html><body><div id="root"></div></body></html>').run()
    assert (result.mode, result.verdict) == ("js-only", "manual")


def test_a_page_with_nothing_machine_readable_is_manual_with_an_explanation():
    result = Setup().site(PLAIN_PAGE).run()
    assert (result.status, result.mode, result.verdict) == ("ok", "manual", "manual")
    assert "a person checks this firm by hand" in " ".join(details(result, "note"))


@pytest.mark.parametrize("blank", ["", "<html><body></body></html>", "   \n  "])
def test_a_page_with_no_text_and_no_links_is_manual_never_empty(blank):
    result = Setup().site(blank, job=None).run(firm(example_job_url=None))
    assert (result.status, result.mode, result.verdict) == ("ok", "manual", "manual")
    assert "no visible text and no links" in " ".join(details(result, "note"))


# -- failures and blocks of the primary page --------------------------------------------------------------


def test_a_missing_page_is_failed_not_empty():
    s = Setup().site(None, job=None)
    s.transport.add(LISTING, reply(404, "gone"))
    result = s.run(firm(example_job_url=None))
    assert (result.status, result.mode, result.verdict) == ("failed", "unknown", "unresolved")
    assert result.error.kind == "http_4xx" and "robots" in kinds(result)


def test_a_403_is_blocked():
    s = Setup().site(None, job=None)
    s.transport.add(LISTING, reply(403, "no"))
    result = s.run(firm(example_job_url=None))
    assert (result.status, result.mode, result.verdict) == ("blocked", "blocked", "blocked")
    assert result.block.reason == "http_403"


def test_a_robots_disallow_is_blocked():
    s = Setup().site(None, job=None, robots="User-agent: *\nDisallow: /jobs")
    result = s.run(firm(example_job_url=None))
    assert (result.status, result.block.reason) == ("blocked", "robots_disallow")
    assert LISTING not in result.requests


def test_a_certificate_error_is_failed_after_one_attempt():
    s = Setup()
    s.transport.add(ROBOTS, TransportError("tls", "certificate verify failed"))
    result = s.run(firm(example_job_url=None))
    assert (result.status, result.error.kind, result.error.retryable) == ("failed", "tls", False)
    assert result.requests == (ROBOTS,)


@pytest.mark.parametrize("url", ["", "not a url", "ftp://example.test/x"])
def test_an_unusable_address_is_failed_with_no_request(url):
    result = Setup().run(firm(careers_url=url, listing_url=None, example_job_url=None))
    assert (result.status, result.error.kind) == ("failed", "invalid_url")
    assert result.requests == ()


def test_the_listing_url_is_preferred_over_the_careers_url():
    s = Setup().site(jobs_page(), job=None)
    result = s.run(firm(example_job_url=None))
    assert LISTING in result.requests and CAREERS not in result.requests


def test_the_careers_url_is_used_when_there_is_no_listing_url():
    s = Setup()
    s.transport.add(ROBOTS, robots_ok()).add(CAREERS, reply(200, jobs_page()))
    result = s.run(firm(listing_url=None, example_job_url=None))
    assert result.requests == (ROBOTS, CAREERS) and result.status == "ok"


def test_a_redirect_is_recorded_as_evidence():
    s = Setup()
    s.transport.add(ROBOTS, robots_ok()).add(LISTING, redirect(HOST + "/jobs-new"))
    s.transport.add(HOST + "/jobs-new", reply(200, jobs_page()))
    result = s.run(firm(example_job_url=None))
    assert "redirect_chain" in kinds(result)
    assert "/jobs-new" in details(result, "redirect_chain")[0]


# -- never LinkedIn or Indeed ---------------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/company/alpha/jobs", "https://fr.indeed.com/jobs?q=intern", "http://INDEED.com/x"])
def test_linkedin_and_indeed_are_never_fetched(url):
    result = Setup().run(firm(careers_url=url, listing_url=None, example_job_url=None, terms_url=TERMS))
    assert (result.status, result.mode, result.verdict) == ("ok", "manual", "manual")
    assert result.requests == ()
    assert "policy" in kinds(result)
    assert result.terms.status == "not_assessed"


def test_a_listing_url_on_linkedin_is_also_never_fetched():
    result = Setup().run(firm(listing_url="https://www.linkedin.com/jobs/x"))
    assert result.requests == () and "policy" in kinds(result)


def test_only_whole_host_labels_count_as_linkedin_or_indeed():
    s = Setup().site(jobs_page(), job=None)
    result = s.run(firm(example_job_url=None, careers_url="https://notindeed.example.test/x"))
    assert "policy" not in kinds(result) and result.requests        # listing_url is still the primary


# -- the example job page and the api_url hint --------------------------------------------------------------------


def test_the_example_job_page_is_not_fetched_twice_when_it_is_the_listing():
    s = Setup().site(JSONLD_PAGE, job=None)
    result = s.run(firm(example_job_url=LISTING))
    assert result.requests == (ROBOTS, LISTING) and result.mode == "json-ld"


def test_a_failing_example_job_page_is_a_note_and_the_firm_is_still_classified():
    s = Setup().site(jobs_page(), job=None)
    s.transport.add(EXAMPLE, reply(404, "gone"))
    result = s.run()
    assert (result.status, result.mode) == ("ok", "html")
    assert "example job page not read: failed (http_4xx)" in " ".join(details(result, "note"))


def test_a_job_page_without_json_ld_says_what_was_read():
    result = Setup().site(jobs_page(), job=PLAIN_PAGE).run()
    assert "no JobPosting JSON-LD found in the raw HTML" in " ".join(details(result, "note"))


def test_no_example_job_url_means_no_job_page_request_and_a_note():
    result = Setup().site(jobs_page(), job=None).run(firm(example_job_url=None))
    assert result.requests == (ROBOTS, LISTING)
    assert "no example job URL" in " ".join(details(result, "note"))


@pytest.mark.parametrize("example", [None, EXAMPLE])
def test_an_api_url_is_noted_and_never_requested(example):
    s = Setup().site(jobs_page(), job=PLAIN_PAGE)
    result = s.run(firm(api_url=HOST + "/api/jobs", example_job_url=example))
    assert details(result, "note")[0] == "api_url provided, not checked in this version"
    assert HOST + "/api/jobs" not in result.requests


def test_an_api_url_is_noted_on_a_policy_result_too():
    result = Setup().run(firm(careers_url="https://www.linkedin.com/x", listing_url=None, api_url=HOST + "/api"))
    assert "api_url provided" in details(result, "note")[0]


# -- terms of use --------------------------------------------------------------------------------------------------


BAN = "<p>You may not use any robot, spider or other automatic means to access this site without permission.</p>"


def test_a_terms_ban_is_flagged_and_never_changes_the_verdict():
    plain = Setup().site(jobs_page()).run()
    s = Setup().site(jobs_page())
    s.transport.add(TERMS, reply(200, f"<html><body>{BAN}</body></html>"))
    flagged = s.run(firm(terms_url=TERMS))
    assert flagged.terms.status == "flagged" and flagged.terms.quotes
    assert (flagged.status, flagged.mode, flagged.verdict) == (plain.status, plain.mode, plain.verdict)


def test_no_terms_url_is_not_assessed_with_a_reason():
    result = Setup().site(jobs_page()).run()
    assert result.terms.status == "not_assessed" and "no terms URL" in result.terms.why


def test_a_failed_terms_fetch_is_not_assessed_and_the_firm_is_unaffected():
    s = Setup().site(jobs_page())
    s.transport.add(TERMS, reply(404, "gone"))
    result = s.run(firm(terms_url=TERMS))
    assert result.status == "ok" and result.terms.status == "not_assessed"
    assert "http_4xx" in result.terms.why


def test_terms_are_still_read_when_the_primary_page_failed():
    s = Setup().site(None, job=None)
    s.transport.add(LISTING, reply(404, "gone")).add(TERMS, reply(200, f"<html><body>{BAN}</body></html>"))
    result = s.run(firm(example_job_url=None, terms_url=TERMS))
    assert result.status == "failed" and result.terms.status == "flagged"


def test_terms_on_a_host_that_blocked_us_are_not_assessed():
    s = Setup().site(None, job=None)
    s.transport.add(LISTING, reply(403, "no"))
    result = s.run(firm(example_job_url=None, terms_url=TERMS))
    assert result.status == "blocked" and result.terms.status == "not_assessed"
    assert "blocked" in result.terms.why and TERMS not in result.requests


# -- budgets ------------------------------------------------------------------------------------------------------------


def test_the_request_budget_skips_later_steps_with_visible_notes():
    s = Setup().site(jobs_page(), job=JSONLD_PAGE, robots=f"{ALLOW_ALL}\nSitemap: {SITEMAP}")
    s.transport.add(TERMS, reply(200, "<html><body>text</body></html>"))
    result = s.run(firm(terms_url=TERMS), max_requests=2)
    assert result.requests == (ROBOTS, LISTING)                  # exactly 2: nothing after the cap
    assert "example job page not fetched: request budget used up (2 of 2)" in " ".join(details(result, "note"))
    assert result.terms.status == "not_assessed" and "request budget" in result.terms.why
    assert result.status == "ok"                                  # classified from the listing alone


def test_the_time_budget_skips_later_steps():
    s = Setup()
    s.transport.add(ROBOTS, robots_ok())
    s.transport.add(LISTING, TransportError("timeout", "slow"), TransportError("timeout", "slow"),
                    reply(200, jobs_page()))                      # two retries: the fetcher waits 2 s + 4 s
    result = s.run(firm(), max_seconds=5)
    assert result.status == "ok" and EXAMPLE not in result.requests
    assert "time budget used up" in " ".join(details(result, "note"))


def test_the_primary_page_is_fetched_even_with_no_budget():
    s = Setup().site(jobs_page(), job=None)
    result = s.run(firm(example_job_url=None), max_requests=0, max_seconds=0)
    assert LISTING in result.requests and result.status == "ok"


def test_the_default_budget_is_ten_requests():
    s = Setup().site(jobs_page(), job=PLAIN_PAGE)
    assert len(s.run().requests) <= 10


def test_requests_of_one_firm_do_not_include_the_previous_firms():
    s = Setup().site(jobs_page(), job=None)
    s.run(firm(example_job_url=None))
    other = "https://other.example.test"
    s.transport.add(other + "/robots.txt", robots_ok()).add(other + "/jobs", reply(200, jobs_page()))
    second = s.run(FirmInput("Beta", other + "/careers", other + "/jobs"))
    assert second.requests == (other + "/robots.txt", other + "/jobs")


# -- the guard ------------------------------------------------------------------------------------------------------------


class BrokenFetcher:
    request_log: tuple = ()

    def fetch(self, url, **kw):
        raise RuntimeError("boom")

    def robots_rules(self, url):
        return None


def test_an_unexpected_exception_becomes_failed_internal_with_the_evidence_so_far():
    result = triage_firm(firm(api_url="https://x.example.test/api"), BrokenFetcher())
    assert (result.status, result.mode, result.verdict) == ("failed", "unknown", "unresolved")
    assert result.error.kind == "internal" and "RuntimeError: boom" in result.error.message
    assert not result.error.retryable
    assert details(result, "note") == ["api_url provided, not checked in this version"]


def test_a_contract_error_is_caught_by_the_guard(monkeypatch):
    import tracker.triage.orchestrator as module

    def bad(*args, **kwargs):
        raise ContractError("our own code built an invalid result")

    monkeypatch.setattr(module, "analyze_listing", bad)
    result = Setup().site(jobs_page()).run()
    assert result.status == "failed" and result.error.kind == "internal"
    assert "ContractError" in result.error.message


# -- results are well-formed -----------------------------------------------------------------------------------------------


def test_every_result_survives_the_json_round_trip():
    s = Setup().site(jobs_page(), job=JSONLD_PAGE)
    result = s.run(firm(terms_url=None))
    assert TriageResult.from_json(result.to_json()) == result


def test_the_robots_rules_are_recorded_as_evidence():
    result = Setup().site(jobs_page()).run()
    assert details(result, "robots") and result.evidence[0].kind == "robots"


# -- T4.2b: manual / js-only is never concluded while a step that could change it did not complete ----------------------------

SHELL = '<html><body><div id="root"></div></body></html>'
FEED_LINK = f'<head><link rel="alternate" type="application/rss+xml" href="{HOST}/feed.xml"></head><body>'


def notes_of(result):
    return " ".join(details(result, "note"))


def test_js_only_with_a_failed_example_job_page_is_unresolved_not_manual():
    s = Setup().site(SHELL, job=None)
    s.transport.add(EXAMPLE, reply(404, "gone"))
    result = s.run()
    assert (result.status, result.mode, result.verdict) == ("failed", "unknown", "unresolved")
    assert result.error.kind == "http_4xx" and not result.error.retryable
    assert "spa_shell" in kinds(result) and "robots" in kinds(result)          # the evidence is kept
    assert "example job page did not complete (http_4xx)" in notes_of(result)
    assert "a person checks this firm by hand" not in notes_of(result)


def test_manual_with_a_job_page_that_times_out_is_a_retryable_failure():
    s = Setup().site(PLAIN_PAGE, job=None)
    s.transport.add(EXAMPLE, TransportError("timeout", "slow"))
    result = s.run()
    assert (result.status, result.error.kind, result.error.retryable) == ("failed", "timeout", True)


def test_manual_with_a_refused_job_page_is_blocked_with_that_reason():
    s = Setup().site(PLAIN_PAGE, job=None)
    s.transport.add(EXAMPLE, reply(403, "no"))
    result = s.run()
    assert (result.status, result.mode, result.verdict) == ("blocked", "blocked", "blocked")
    assert result.block.reason == "http_403" and result.block.url == EXAMPLE


def test_manual_with_a_job_page_disallowed_by_robots_is_blocked():
    s = Setup().site(PLAIN_PAGE, job=None, robots="User-agent: *\nDisallow: /jobs/")
    result = s.run()
    assert (result.status, result.block.reason) == ("blocked", "robots_disallow")
    assert EXAMPLE not in result.requests


def test_manual_with_a_failed_feed_is_unresolved():
    s = Setup().site(PLAIN_PAGE.replace("<body>", FEED_LINK))
    s.transport.add(HOST + "/feed.xml", reply(404, "gone"))
    result = s.run()
    assert (result.status, result.error.kind, result.verdict) == ("failed", "http_4xx", "unresolved")
    assert "feed did not complete" in notes_of(result)


def test_manual_with_a_failed_sitemap_is_unresolved():
    s = Setup().site(PLAIN_PAGE, robots=f"{ALLOW_ALL}\nSitemap: {SITEMAP}")
    s.transport.add(SITEMAP, reply(404, "gone"))
    result = s.run()
    assert (result.status, result.error.kind) == ("failed", "http_4xx")
    assert "sitemap did not complete" in notes_of(result)


def test_manual_with_a_sitemap_disallowed_by_robots_is_blocked():
    s = Setup().site(PLAIN_PAGE, robots=f"User-agent: *\nDisallow: /sitemap\nSitemap: {SITEMAP}")
    result = s.run()
    assert (result.status, result.block.reason) == ("blocked", "robots_disallow")


def test_a_skipped_step_because_of_the_request_budget_makes_manual_unresolved():
    s = Setup().site(PLAIN_PAGE, job=PLAIN_PAGE)
    result = s.run(max_requests=2)
    assert (result.status, result.error.kind, result.error.retryable) == ("failed", "budget", True)
    assert EXAMPLE not in result.requests and result.verdict == "unresolved"


def test_a_skipped_step_because_of_the_time_budget_makes_js_only_unresolved():
    s = Setup()
    s.transport.add(ROBOTS, robots_ok())
    s.transport.add(LISTING, TransportError("timeout", "slow"), TransportError("timeout", "slow"), reply(200, SHELL))
    result = s.run(max_seconds=5)
    assert (result.status, result.error.kind) == ("failed", "budget")
    assert "time budget used up" in result.error.message


def test_the_first_incomplete_step_in_step_order_gives_the_error():
    s = Setup().site(PLAIN_PAGE.replace("<body>", FEED_LINK), job=None, robots=f"{ALLOW_ALL}\nSitemap: {SITEMAP}")
    s.transport.add(EXAMPLE, reply(404, "gone")).add(HOST + "/feed.xml", TransportError("timeout", "slow"))
    s.transport.add(SITEMAP, reply(404, "gone"))
    result = s.run()
    assert result.error.kind == "http_4xx" and result.error.url == EXAMPLE            # the job page comes first
    assert HOST + "/feed.xml" in result.requests and SITEMAP in result.requests        # later steps still ran


@pytest.mark.parametrize("page, job, expected_mode", [(PLAIN_PAGE, PLAIN_PAGE, "manual"), (SHELL, PLAIN_PAGE, "js-only")])
def test_honest_not_founds_stay_manual_or_js_only(page, job, expected_mode):
    result = Setup().site(page, job=job).run()                  # no feed link, no Sitemap line: pages read, nothing found
    assert (result.status, result.mode, result.verdict) == ("ok", expected_mode, "manual")


def test_no_example_url_is_an_honest_not_found_not_an_incomplete_step():
    result = Setup().site(PLAIN_PAGE, job=None).run(firm(example_job_url=None))
    assert (result.status, result.mode) == ("ok", "manual")


def test_a_failed_terms_page_never_makes_a_manual_firm_unresolved():
    s = Setup().site(PLAIN_PAGE)
    s.transport.add(TERMS, reply(404, "gone"))
    result = s.run(firm(terms_url=TERMS))
    assert (result.status, result.mode) == ("ok", "manual") and result.terms.status == "not_assessed"


@pytest.mark.parametrize("page, expected", [(jobs_page(), "html"), (PLAIN_PAGE.replace(
    "About</a>", "About</a><a href='https://acme.myworkdayjobs.com/en/site'>Jobs</a>"), "vendor")])
def test_a_firm_with_a_counting_verdict_stays_ok_when_a_step_fails(page, expected):
    s = Setup().site(page, job=None)
    s.transport.add(EXAMPLE, reply(404, "gone"))
    result = s.run()
    assert (result.status, result.mode, result.verdict) == ("ok", expected, "probable")
    assert "example job page not read" in notes_of(result)


def test_an_unresolved_result_still_carries_the_terms_flag_and_the_requests():
    s = Setup().site(SHELL, job=None)
    s.transport.add(EXAMPLE, reply(404, "gone")).add(TERMS, reply(200, f"<html><body>{BAN}</body></html>"))
    result = s.run(firm(terms_url=TERMS))
    assert result.terms.status == "flagged"
    assert result.requests == (ROBOTS, LISTING, EXAMPLE, TERMS)
    assert TriageResult.from_json(result.to_json()) == result
