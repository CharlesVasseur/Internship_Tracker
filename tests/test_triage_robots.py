"""Tests for src/tracker/triage/robots.py (step T2.2). Offline.

Every robots.txt body in this file is SYNTHETIC. The first group below rebuilds, with made-up
paths and hosts, three situations first seen on real sites during the survey: a wildcard Allow that
the standard library parser misreads, a Crawl-delay written in lowercase together with a Sitemap
line, and a Crawl-delay that belongs to another crawler's group.
"""

import random

import pytest

from tracker.triage.robots import (
    MAX_CRAWL_DELAY_SECONDS,
    RobotsRules,
    agent_token,
    parse_robots,
    wait_seconds,
)

UA = "InternshipTracker/0.1 (triage; contact test@example.com)"
TOKEN = agent_token(UA)


def synthetic(text: str) -> RobotsRules:
    return parse_robots(text, TOKEN)


# -- three situations seen on real sites, rebuilt with made-up data ----------------------

WILDCARD_ALLOW = """\
User-agent: *
Disallow: /
Allow: */careers-board/
Disallow: */careers-board/*/styles/
Disallow: */careers-board-view/
"""

LOWERCASE_DELAY = """\
User-agent: *
crawl-delay: 5
Disallow:
Sitemap: http://jobs.example.test/sitemap.xml
"""

DELAY_FOR_ANOTHER_CRAWLER = """\
User-agent: ExampleBot
Crawl-delay: 10
Disallow: /

User-agent: *
Disallow: /admin/
Allow: /admin/public.php
"""

BOARD = "https://jobs.example.test/ACME1/careers-board/0001/"


def test_a_wildcard_allow_rule_lets_the_listing_through():
    """The stdlib parser said False here. Under RFC 9309 the longer Allow wins."""
    assert synthetic(WILDCARD_ALLOW).allowed(BOARD) is True


def test_with_that_wildcard_allow_everything_else_and_the_excluded_paths_stay_disallowed():
    rules = synthetic(WILDCARD_ALLOW)
    assert rules.allowed("https://jobs.example.test/") is False
    assert rules.allowed("https://jobs.example.test/other/page") is False
    assert rules.allowed(BOARD + "styles/x.css") is False
    assert rules.allowed("https://jobs.example.test/ACME1/careers-board-view/x") is False


def test_a_lowercase_crawl_delay_a_sitemap_line_and_an_open_site():
    rules = synthetic(LOWERCASE_DELAY)
    assert rules.crawl_delay == 5.0                       # written in lowercase in the file
    assert rules.sitemaps == ("http://jobs.example.test/sitemap.xml",)
    assert rules.allowed("https://jobs.example.test/jobs/anything") is True


def test_a_crawl_delay_that_belongs_to_another_crawlers_group_is_not_ours():
    rules = synthetic(DELAY_FOR_ANOTHER_CRAWLER)
    assert rules.matched_group == "*"
    assert rules.crawl_delay is None


def test_a_longer_allow_beats_a_shorter_disallow():
    rules = synthetic(DELAY_FOR_ANOTHER_CRAWLER)
    assert rules.allowed("https://x.test/offers") is True
    assert rules.allowed("https://x.test/admin/options.php") is False
    assert rules.allowed("https://x.test/admin/public.php") is True


# -- matching rules of the standard -------------------------------------------------

def test_plain_prefix_rule():
    rules = synthetic("User-agent: *\nDisallow: /private")
    assert rules.allowed("https://x.test/private/a") is False
    assert rules.allowed("https://x.test/privateer") is False     # prefix match, as in the standard
    assert rules.allowed("https://x.test/public") is True


def test_star_wildcard_in_the_middle():
    rules = synthetic("User-agent: *\nDisallow: /a/*/secret")
    assert rules.allowed("https://x.test/a/1/secret") is False
    assert rules.allowed("https://x.test/a/1/2/secret/x") is False
    assert rules.allowed("https://x.test/a/1/open") is True


def test_dollar_anchors_the_end():
    rules = synthetic("User-agent: *\nDisallow: /*.pdf$")
    assert rules.allowed("https://x.test/a/file.pdf") is False
    assert rules.allowed("https://x.test/a/file.pdf?x=1") is True    # query makes it not end in .pdf
    assert rules.allowed("https://x.test/a/file.pdfx") is True


def test_query_string_is_part_of_what_is_matched():
    rules = synthetic("User-agent: *\nDisallow: /*?session=")
    assert rules.allowed("https://x.test/p?session=1") is False
    assert rules.allowed("https://x.test/p?other=1") is True


def test_longest_pattern_wins_whatever_the_order():
    a = synthetic("User-agent: *\nDisallow: /docs\nAllow: /docs/public")
    b = synthetic("User-agent: *\nAllow: /docs/public\nDisallow: /docs")
    for rules in (a, b):
        assert rules.allowed("https://x.test/docs/public/a") is True
        assert rules.allowed("https://x.test/docs/private") is False


def test_tie_goes_to_allow():
    rules = synthetic("User-agent: *\nDisallow: /same\nAllow: /same")
    assert rules.allowed("https://x.test/same") is True


def test_empty_disallow_means_everything_is_allowed():
    assert synthetic("User-agent: *\nDisallow:").allowed("https://x.test/anything") is True


def test_disallow_all():
    rules = synthetic("User-agent: *\nDisallow: /")
    assert rules.allowed("https://x.test/") is False
    assert rules.allowed("https://x.test/a/b") is False


def test_robots_txt_itself_is_always_fetchable():
    rules = synthetic("User-agent: *\nDisallow: /")
    assert rules.allowed("https://x.test/robots.txt") is True


def test_rule_matching_is_case_sensitive_for_paths():
    rules = synthetic("User-agent: *\nDisallow: /Private")
    assert rules.allowed("https://x.test/private") is True


# -- which group applies ---------------------------------------------------------------

def test_our_own_group_beats_the_star_group():
    rules = synthetic("User-agent: *\nDisallow: /\n\nUser-agent: InternshipTracker\nDisallow: /secret")
    assert rules.matched_group == TOKEN
    assert rules.allowed("https://x.test/open") is True
    assert rules.allowed("https://x.test/secret") is False


def test_other_crawlers_groups_are_ignored():
    rules = synthetic("User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nDisallow: /tmp")
    assert rules.allowed("https://x.test/page") is True


def test_several_user_agent_lines_share_one_group():
    rules = synthetic("User-agent: Foo\nUser-agent: InternshipTracker\nDisallow: /x")
    assert rules.allowed("https://x.test/x") is False


def test_two_groups_for_our_token_are_merged():
    text = "User-agent: InternshipTracker\nDisallow: /a\n\nUser-agent: InternshipTracker\nDisallow: /b"
    rules = synthetic(text)
    assert rules.allowed("https://x.test/a") is False
    assert rules.allowed("https://x.test/b") is False


def test_no_group_for_us_and_no_star_group_means_no_rules():
    rules = synthetic("User-agent: GPTBot\nDisallow: /")
    assert rules.matched_group == ""
    assert rules.allowed("https://x.test/anything") is True


def test_blank_lines_and_comments_do_not_break_groups():
    text = "# hello\nUser-agent: *   # everyone\n\nDisallow: /a  # secret\n"
    assert synthetic(text).allowed("https://x.test/a") is False


def test_field_names_are_case_insensitive():
    assert synthetic("USER-AGENT: *\nDISALLOW: /a").allowed("https://x.test/a") is False


def test_rule_before_any_user_agent_line_is_ignored_with_a_note():
    rules = synthetic("Disallow: /a\nUser-agent: *\nDisallow: /b")
    assert rules.allowed("https://x.test/a") is True
    assert any("before any User-agent" in note for note in rules.notes)


# -- unclear files are never read as permission ---------------------------------------------

def test_an_html_page_instead_of_robots_is_unreadable_and_allows_nothing():
    rules = synthetic("<!DOCTYPE html><html><title>404: Not Found</title></html>")
    assert rules.readable is False
    assert rules.allowed("https://x.test/anything") is False
    assert "unreadable" in rules.describe()


def test_empty_file_and_comment_only_file_are_readable_with_no_rules():
    for text in ("", "   \n\n", "# nothing to see\n"):
        rules = synthetic(text)
        assert rules.readable is True
        assert rules.allowed("https://x.test/a") is True


def test_disallow_without_leading_slash_is_read_strictly():
    rules = synthetic("User-agent: *\nDisallow: admin")
    assert rules.allowed("https://x.test/admin/panel") is False
    assert any("no leading '/'" in note for note in rules.notes)


def test_allow_without_leading_slash_does_not_widen_access():
    rules = synthetic("User-agent: *\nDisallow: /\nAllow: public")
    assert rules.allowed("https://x.test/public") is False


def test_a_broken_url_is_not_allowed():
    assert synthetic("User-agent: *\nDisallow:").allowed("http://[bad") is False


# -- crawl-delay, sitemap, helpers ----------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Crawl-delay: 5", 5.0), ("crawl-delay: 0.5", 0.5), ("Crawl-delay: 3s", 3.0),
    ("Crawl-delay: soon", None), ("Crawl-delay: -2", None),
])
def test_crawl_delay_parsing(text, expected):
    assert synthetic("User-agent: *\n" + text).crawl_delay == expected


def test_unreadable_crawl_delay_leaves_a_note():
    assert any("not understood" in n for n in synthetic("User-agent: *\nCrawl-delay: soon").notes)


def test_sitemap_lines_are_kept_as_written_wherever_they_are():
    text = "Sitemap: https://x.test/Sitemap.XML\nUser-agent: *\nDisallow:\nSitemap: https://x.test/two.xml"
    assert synthetic(text).sitemaps == ("https://x.test/Sitemap.XML", "https://x.test/two.xml")


def test_wait_seconds_is_the_larger_of_one_second_and_the_delay():
    assert wait_seconds(None) == (1.0, False)
    assert wait_seconds(0.5) == (1.0, False)
    assert wait_seconds(5.0) == (5.0, False)


def test_wait_seconds_flags_a_delay_over_the_cap_and_the_cap_is_adjustable():
    assert MAX_CRAWL_DELAY_SECONDS == 10.0
    assert wait_seconds(10.0) == (10.0, False)
    assert wait_seconds(30.0) == (30.0, True)
    assert wait_seconds(30.0, cap=60.0) == (30.0, False)


def test_agent_token_from_the_user_agent():
    assert agent_token(UA) == "internshiptracker"
    assert agent_token("InternshipTracker") == "internshiptracker"


def test_allow_all_is_for_a_missing_robots_file():
    rules = RobotsRules.allow_all()
    assert rules.allowed("https://x.test/anything") is True
    assert rules.readable is True


def test_describe_is_short_and_mentions_the_essentials():
    line = synthetic(LOWERCASE_DELAY).describe()
    assert "Crawl-delay 5s" in line and "1 Sitemap line" in line and len(line) < 300


# -- never raises ------------------------------------------------------------------------------

def test_random_text_never_raises():
    rng = random.Random(7)
    pieces = ["User-agent:", "Disallow:", "Allow:", "Crawl-delay:", "Sitemap:", "*", "$", "/", "#",
              "\n", " ", "a", "/a/*/b$", "InternshipTracker", "é", ":"]
    for _ in range(400):
        text = "".join(rng.choice(pieces) for _ in range(rng.randrange(0, 40)))
        rules = parse_robots(text, TOKEN)
        assert isinstance(rules.allowed("https://x.test/a/b?c=1"), bool)

