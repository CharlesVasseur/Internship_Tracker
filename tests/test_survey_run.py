"""Step 4/5 tests: whole-firm survey and command line, on a fake website.
No test opens the network: every request goes to FakeSite."""

import pytest

import scripts.source_survey as survey
from scripts.source_survey import (
    HttpClient,
    RawResponse,
    load_survey_rows,
    render_survey,
    survey_firm,
)

HTML = {"content-type": "text/html; charset=utf-8"}
TEXT = {"content-type": "text/plain"}

ROBOTS = "User-agent: *\nDisallow: /private\nSitemap: https://www.acme.test/sitemap.xml\n"
CAREERS_HTML = "<html><body><a href='/jobs'>Our jobs</a></body></html>"
LISTING_HTML = (
    '<html><head><link rel="alternate" type="application/rss+xml" href="/jobs.rss"></head>'
    "<body><ul><li>Actuarial Intern - Paris</li></ul>"
    '<a href="https://acme.wd3.myworkdayjobs.com/en/job/1">Apply</a></body></html>'
)
JOB_HTML = (
    '<html><head><script type="application/ld+json">'
    '{"@type": "JobPosting", "title": "Actuarial Intern"}'
    "</script></head><body>Actuarial Intern</body></html>"
)
FILLER = "<p>" + ("This page explains how you may use our website and its content. " * 12) + "</p>"
TERMS_HTML = f"<html><body>{FILLER}<p>You may not use any robot or spider to access the site.</p></body></html>"

ROW = {
    "firm": "Acme",
    "careers_entry_url": "https://www.acme.test/careers",
    "listing_url": "https://www.acme.test/jobs",
    "example_job_url": "https://www.acme.test/jobs/1",
    "example_job_title": "Actuarial Intern",
    "terms_url": "https://www.acme.test/terms",
}


class FakeSite:
    """A fake transport: url -> RawResponse or an exception. Records every call."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []
        self.user_agents = []

    def __call__(self, url, headers):
        self.calls.append(url)
        self.user_agents.append(headers.get("User-Agent", ""))
        answer = self.routes.get(url)
        if answer is None:
            return RawResponse(404, HTML, b"not found")
        if isinstance(answer, Exception):
            raise answer
        return answer


def page(html, status=200, headers=HTML):
    return RawResponse(status, headers, html.encode("utf-8"))


def good_site(**overrides):
    routes = {
        "https://www.acme.test/robots.txt": page(ROBOTS, headers=TEXT),
        "https://www.acme.test/careers": page(CAREERS_HTML),
        "https://www.acme.test/jobs": page(LISTING_HTML),
        "https://www.acme.test/jobs/1": page(JOB_HTML),
        "https://www.acme.test/terms": page(TERMS_HTML),
    }
    routes.update(overrides)
    return FakeSite(routes)


def make_client(site):
    return HttpClient(
        "Test/1 (contact test@example.com)",
        transport=site,
        sleeper=lambda seconds: None,
        monotonic=lambda: 0.0,
    )


def run(site, **row_changes):
    row = {**ROW, **row_changes}
    client = make_client(site)
    return survey_firm(client, row, "2026-10-12"), client


# ---- One firm ---------------------------------------------------------------


def test_full_survey_fills_every_scripted_field():
    fields, _ = run(good_site())
    assert fields.listing_status == "200"
    assert fields.listing_final_url == "https://www.acme.test/jobs"
    assert fields.robots_host == "https://www.acme.test"
    assert fields.robots_result == "allowed"
    assert "Disallow: /private" in fields.robots_paste
    assert "Sitemap: https://www.acme.test/sitemap.xml" in fields.robots_paste
    assert fields.jsonld_status == "yes"
    assert '"title": "Actuarial Intern"' in fields.jsonld_excerpt
    assert "jobs.rss" in fields.rss_sitemap and "contents not checked" in fields.rss_sitemap
    assert fields.job_page_domain == "www.acme.test"
    assert "workday" in fields.vendors_found
    assert fields.conclusion == "likely"
    assert fields.title_in_source == "yes"
    assert fields.terms_mentions == "yes"
    assert "[English, guessed]" in fields.terms_sentences
    assert fields.xhr_note.startswith("MANUAL") and fields.notes.startswith("MANUAL")
    assert fields.apply_domains.startswith("MANUAL")


def test_full_survey_renders_with_the_real_template():
    fields, _ = run(good_site())
    text = render_survey(fields)
    assert text.startswith("FIRM: Acme, MANUAL: to be filled by Charles")
    assert "Checked on: 2026-10-12" in text
    assert "<url>" not in text and "<host>" not in text


def test_missing_urls_are_reported_not_hidden():
    fields, client = run(
        good_site(), careers_entry_url="", example_job_url="", terms_url="", example_job_title=""
    )
    assert fields.jsonld_status == "no posting to test"
    assert fields.job_page_domain == "n/a (no example job URL)"
    assert fields.terms_mentions == "terms page not found"
    assert fields.title_in_source == "failed: example_job_title missing in survey_input.csv"
    # careers page was not checked, so "not found" must not be claimed for item 5
    assert fields.rss_sitemap.startswith("found:")
    assert "careers entry page: failed: missing in survey_input.csv" in fields.rss_sitemap
    assert "no example job URL" in fields.robots_result


def test_feed_not_found_only_when_everything_was_checked():
    site = good_site(**{
        "https://www.acme.test/robots.txt": page("User-agent: *\nDisallow:\n", headers=TEXT),
        "https://www.acme.test/jobs": page("<html><body>Actuarial Intern</body></html>"),
    })
    fields, _ = run(site)
    assert fields.rss_sitemap.startswith("not found")
    assert fields.vendors_found.startswith("none found")
    assert fields.conclusion == "unknown"


def test_403_on_listing_is_blocked_and_the_host_is_left_alone():
    site = good_site(**{"https://www.acme.test/jobs": page("denied", status=403)})
    fields, client = run(site)
    assert fields.listing_status == "403 (blocked)"
    assert fields.jsonld_status == "blocked"
    assert fields.terms_mentions == "blocked"
    assert "blocked: listing page" in fields.obstacles
    # nothing is requested from the host after the 403
    assert client.request_log[-1] == "https://www.acme.test/jobs"


def test_timeout_on_job_page_is_failed_never_no():
    site = good_site(**{"https://www.acme.test/jobs/1": TimeoutError("timeout")})
    fields, _ = run(site)
    assert fields.jsonld_status == "failed: timeout"
    assert fields.job_page_domain == "failed: timeout"


def test_404_listing_is_failed_not_an_empty_listing():
    site = good_site(**{"https://www.acme.test/jobs": page("gone", status=404)})
    fields, _ = run(site)
    assert fields.listing_status == "404"
    assert fields.title_in_source == "failed: HTTP 404"
    assert "listing page: failed: HTTP 404" in fields.rss_sitemap


def test_cross_host_redirect_shows_the_target_and_is_not_followed():
    site = good_site(**{
        "https://www.acme.test/jobs": RawResponse(302, {"location": "https://other.test/x"}, b""),
    })
    fields, client = run(site)
    assert fields.listing_status == "302 (cross-host redirect not followed)"
    assert "redirects to https://other.test/x" in fields.listing_final_url
    assert not any("other.test" in url for url in client.request_log)


def test_robots_disallow_is_reported_and_pages_are_not_fetched():
    site = good_site(**{
        "https://www.acme.test/robots.txt": page("User-agent: *\nDisallow: /jobs\n", headers=TEXT),
    })
    fields, client = run(site)
    assert fields.robots_result == "disallowed for the listing path / disallowed for job pages"
    assert fields.listing_status == "disallowed"
    assert fields.jsonld_status == "disallowed"
    assert "https://www.acme.test/jobs" not in client.request_log


def test_robots_404_means_allowed_and_is_stated():
    site = good_site(**{"https://www.acme.test/robots.txt": page("nope", status=404)})
    fields, _ = run(site)
    assert fields.robots_result == "no robots.txt (404)"
    assert fields.robots_paste == "n/a (no robots.txt)"


def test_unreachable_robots_stops_the_host_and_says_so():
    site = good_site(**{"https://www.acme.test/robots.txt": page("oops", status=503)})
    fields, client = run(site)
    assert fields.robots_result.startswith("failed")
    assert "failed: robots.txt HTTP 503" in fields.listing_status
    assert "https://www.acme.test/jobs" not in client.request_log


def test_other_host_gets_its_own_robots_check():
    site = good_site(**{
        "https://jobs.vendor.test/robots.txt": page("User-agent: *\nDisallow:\n", headers=TEXT),
        "https://jobs.vendor.test/p/1": page(JOB_HTML),
    })
    fields, client = run(site, example_job_url="https://jobs.vendor.test/p/1")
    assert "https://jobs.vendor.test/robots.txt" in client.request_log
    assert "example job page is on https://jobs.vendor.test" in fields.extra_hosts_note
    assert "another host" in fields.robots_result


def test_terms_pdf_is_failed_not_no():
    site = good_site(**{
        "https://www.acme.test/terms": RawResponse(200, {"content-type": "application/pdf"}, b"%PDF-1.4 \xff\xfe"),
    })
    fields, _ = run(site)
    assert fields.terms_mentions == "failed: not HTML (application/pdf)"


def test_url_without_scheme_is_rejected_before_any_request():
    site = good_site()
    fields, client = run(site, listing_url="www.acme.test/jobs")
    assert fields.listing_status.startswith("failed: invalid URL")
    assert "https://www.acme.test/jobs" not in client.request_log


# ---- CSV ---------------------------------------------------------------------


def write_csv(path, *rows, header=None):
    header = header or ";".join(survey.CSV_COLUMNS)
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def csv_line(row):
    return ";".join(row[column] for column in survey.CSV_COLUMNS)


def test_optional_country_column_is_accepted(tmp_path):
    path = tmp_path / "in.csv"
    write_csv(path, csv_line(ROW) + ";France", header=";".join(survey.CSV_COLUMNS) + ";country")
    rows = load_survey_rows(path)
    fields = survey.fields_from_csv_row(rows[0])
    assert fields.country == "France"


def test_wrong_header_is_rejected(tmp_path):
    path = tmp_path / "in.csv"
    write_csv(path, "x", header="firm;url")
    with pytest.raises(ValueError, match="CSV header must be exactly"):
        load_survey_rows(path)


# ---- Command line -----------------------------------------------------------


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(survey, "REPO_ROOT", tmp_path)
    write_csv(tmp_path / "survey_input.csv", csv_line(ROW))
    return tmp_path


def no_network(url, headers):
    raise AssertionError("the dry run must not touch the network")


def test_dry_run_writes_to_dryrun_folder_and_contacts_nothing(repo):
    code = survey.main([], transport=no_network)
    assert code == 0
    assert (repo / "survey_dryrun" / "acme.md").exists()
    assert not (repo / "survey_output").exists()


def test_live_refuses_the_placeholder_address(repo, monkeypatch):
    monkeypatch.delenv("SURVEY_CONTACT", raising=False)
    with pytest.raises(SystemExit) as exc:
        survey.main(["--live"], transport=no_network)
    assert "<MY_EMAIL>" in str(exc.value)


def test_invalid_contact_is_rejected(repo):
    with pytest.raises(SystemExit):
        survey.main(["--live", "--contact", "not an address"], transport=no_network)


def test_live_run_writes_files_and_a_request_log(repo):
    site = good_site()
    code = survey.main(
        ["--live", "--contact", "me@example.com"],
        transport=site,
        sleeper=lambda seconds: None,
        monotonic=lambda: 0.0,
    )
    assert code == 0
    survey_text = (repo / "survey_output" / "acme.md").read_text(encoding="utf-8")
    assert "JSON-LD JobPosting on the example job page: yes" in survey_text
    log = (repo / "survey_output" / "_run_log.txt").read_text(encoding="utf-8")
    assert "https://www.acme.test/robots.txt" in log
    assert "me@example.com" in log
    assert all("me@example.com" in agent for agent in site.user_agents)


def test_only_runs_one_firm(repo):
    other = {**ROW, "firm": "Beta", "listing_url": "", "careers_entry_url": "", "example_job_url": "", "terms_url": ""}
    write_csv(repo / "survey_input.csv", csv_line(ROW), csv_line(other))
    survey.main(
        ["--live", "--contact", "me@example.com", "--only", "Beta"],
        transport=good_site(),
        sleeper=lambda seconds: None,
        monotonic=lambda: 0.0,
    )
    assert (repo / "survey_output" / "beta.md").exists()
    assert not (repo / "survey_output" / "acme.md").exists()


def test_unknown_only_name_stops_the_run(repo):
    with pytest.raises(SystemExit, match="no such firm"):
        survey.main(["--only", "Nobody"], transport=no_network)


def test_one_bad_row_does_not_stop_the_others_and_is_reported(repo, capsys):
    bad = {**ROW, "firm": ""}
    write_csv(repo / "survey_input.csv", csv_line(bad), csv_line(ROW))
    code = survey.main(
        ["--live", "--contact", "me@example.com"],
        transport=good_site(),
        sleeper=lambda seconds: None,
        monotonic=lambda: 0.0,
    )
    assert code == 1
    assert (repo / "survey_output" / "acme.md").exists()
    assert "firm name is empty" in capsys.readouterr().err
