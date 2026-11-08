"""Step 3 tests: parsers only. Strings in, facts out. No network."""

import json

from scripts.source_survey import (
    FetchResult,
    assess_terms,
    find_feed_links,
    find_jobposting_jsonld,
    find_obstacle_hints,
    find_vendor_hits,
    all_referenced_hosts,
    outbound_link_hosts,
    guess_language,
    page_problem,
    title_in_html,
    vendor_conclusion,
)


def ld_page(payload: str) -> str:
    return (
        "<html><head>"
        f'<script type="application/ld+json">{payload}</script>'
        "</head><body>Job</body></html>"
    )


def fetch(status=200, content_type="text/html; charset=utf-8", outcome="ok", text="<html></html>"):
    return FetchResult(
        url="https://x.example/a",
        final_url="https://x.example/a",
        status=status,
        headers={"content-type": content_type},
        body=b"",
        text=text if outcome == "ok" else None,
        outcome=outcome,
    )


# ---- page_problem and FetchResult.ok ---------------------------------------


def test_good_html_page_has_no_problem():
    assert page_problem(fetch()) is None


def test_404_page_is_a_problem_not_a_page_to_parse():
    result = fetch(status=404)
    assert page_problem(result) == "failed: HTTP 404"
    assert result.ok is False


def test_pdf_is_reported_as_not_html():
    assert page_problem(fetch(content_type="application/pdf")) == "failed: not HTML (application/pdf)"


def test_pdf_that_failed_to_decode_is_still_reported_as_not_html():
    result = fetch(content_type="application/pdf", outcome="failed: decode error (bad byte)")
    assert page_problem(result) == "failed: not HTML (application/pdf)"


def test_blocked_and_disallowed_pass_through():
    assert page_problem(fetch(outcome="blocked", status=403)) == "blocked"
    assert page_problem(fetch(outcome="disallowed", status=None)) == "disallowed"


# ---- Item 4: JSON-LD -------------------------------------------------------


def test_jsonld_plain_jobposting():
    page = ld_page('{"@context": "https://schema.org", "@type": "JobPosting", "title": "Intern"}')
    status, excerpt = find_jobposting_jsonld(page)
    assert status == "yes"
    assert '"title": "Intern"' in excerpt


def test_jsonld_inside_graph():
    payload = json.dumps(
        {"@graph": [{"@type": "Organization", "name": "Firm"}, {"@type": "JobPosting", "title": "Actuarial intern"}]}
    )
    status, excerpt = find_jobposting_jsonld(ld_page(payload))
    assert status == "yes"
    assert "Actuarial intern" in excerpt


def test_jsonld_type_as_list_and_prefixed():
    assert find_jobposting_jsonld(ld_page('{"@type": ["Thing", "JobPosting"]}'))[0] == "yes"
    assert find_jobposting_jsonld(ld_page('{"@type": "schema:JobPosting"}'))[0] == "yes"
    assert find_jobposting_jsonld(ld_page('{"@type": "https://schema.org/JobPosting"}'))[0] == "yes"


def test_jsonld_top_level_array():
    assert find_jobposting_jsonld(ld_page('[{"@type": "WebSite"}, {"@type": "JobPosting"}]'))[0] == "yes"


def test_page_without_jsonld_is_no():
    assert find_jobposting_jsonld("<html><body>No data</body></html>") == ("no", "")


def test_jsonld_of_another_type_is_no():
    assert find_jobposting_jsonld(ld_page('{"@type": "Organization"}')) == ("no", "")


def test_broken_jsonld_is_failed_never_no():
    status, _ = find_jobposting_jsonld(ld_page('{"@type": "JobPosting", '))
    assert status.startswith("failed: invalid JSON-LD")


def test_broken_block_does_not_hide_a_valid_posting_in_another_block():
    page = (
        '<script type="application/ld+json">{broken</script>'
        '<script type="application/ld+json">{"@type": "JobPosting"}</script>'
    )
    assert find_jobposting_jsonld(page)[0] == "yes"


def test_excerpt_is_at_most_30_lines():
    big = {"@type": "JobPosting", **{f"field{i}": i for i in range(100)}}
    _, excerpt = find_jobposting_jsonld(ld_page(json.dumps(big)))
    assert len(excerpt.splitlines()) == 30


# ---- Item 5: feeds ----------------------------------------------------------


def test_rss_link_found_and_made_absolute():
    page = '<head><link rel="alternate" type="application/rss+xml" href="/jobs.rss"></head>'
    assert find_feed_links(page, "https://x.example/careers/") == ["https://x.example/jobs.rss"]


def test_atom_link_found():
    page = '<link rel="alternate" type="application/atom+xml" href="https://x.example/feed">'
    assert find_feed_links(page, "https://x.example/") == ["https://x.example/feed"]


def test_language_alternate_is_not_a_feed():
    page = '<link rel="alternate" hreflang="fr" href="/fr/">'
    assert find_feed_links(page, "https://x.example/") == []


def test_no_links_gives_empty_list():
    assert find_feed_links("<html><body>hi</body></html>", "https://x.example/") == []


# ---- Item 6: vendors ---------------------------------------------------------


def test_vendor_found_in_host():
    hits = find_vendor_hits({"firm.wd3.myworkdayjobs.com", "www.firm.com"})
    assert hits == {"workday": ["firm.wd3.myworkdayjobs.com"]}
    assert vendor_conclusion(hits) == "likely"


def test_word_however_in_page_text_does_not_trigger_ever():
    page = "<html><body><p>However, whenever you apply, every job is open.</p></body></html>"
    hits = find_vendor_hits(all_referenced_hosts(page, "https://x.example/"))
    assert hits == {}
    assert vendor_conclusion(hits) == "unknown"


def test_ever_needs_an_exact_hostname_label():
    # The two hostnames below are made-up test inputs, not real vendor domains.
    assert "ever" in find_vendor_hits({"careers.ever.example"})
    assert "ever" not in find_vendor_hits({"everyone.example", "forever.example"})


def test_hosts_come_from_href_src_and_form_action():
    page = (
        '<a href="https://jobs.smartrecruiters.com/x">a</a>'
        '<script src="//cdn.example/app.js"></script>'
        '<form action="https://boards.greenhouse.io/post"></form>'
    )
    hosts = all_referenced_hosts(page, "https://x.example/")
    assert hosts == {"jobs.smartrecruiters.com", "cdn.example", "boards.greenhouse.io"}


def test_outbound_hosts_exclude_own_host_and_use_links_only():
    page = (
        '<a href="/local">l</a>'
        '<a href="https://apply.workable.com/x">apply</a>'
        '<img src="https://img.example/a.png">'
    )
    assert outbound_link_hosts(page, "https://x.example/job/1") == ["apply.workable.com"]


# ---- Item 7a: title ------------------------------------------------------------


def test_title_found_despite_entities_accents_and_case():
    page = "<li>Stagiaire Actuariat &amp; Risques - Paris</li>"
    assert title_in_html("stagiaire actuariat & risques", page) is True
    assert title_in_html("Stagiaire Actuariat & Risqués", page) is True


def test_title_missing_is_false():
    assert title_in_html("Actuarial Intern", "<div id='app'></div>") is False


def test_empty_title_is_none_not_false():
    assert title_in_html("  ", "<html></html>") is None


# ---- Item 8: hints ---------------------------------------------------------------


def test_captcha_and_password_hints():
    page = '<div class="g-recaptcha"></div><input type="password">'
    hints = find_obstacle_hints(page)
    assert any("CAPTCHA" in hint for hint in hints)
    assert any("password" in hint for hint in hints)


def test_clean_page_has_no_hints():
    assert find_obstacle_hints("<html><body>Jobs</body></html>") == []


# ---- Item 10: terms -------------------------------------------------------------

FILLER = "<p>" + ("This page describes how you may use our website and its content. " * 12) + "</p>"


def terms_page(sentence: str) -> str:
    return f"<html><body>{FILLER}<p>{sentence}</p>{FILLER}</body></html>"


def test_terms_english():
    result = assess_terms(terms_page("You may not use any robot, spider or automated means to access the site."))
    assert result.status == "yes"
    assert result.sentences[0][1] == "English"


def test_terms_french():
    result = assess_terms(
        terms_page("Il est interdit d'utiliser des robots, des outils d'extraction automatisée ou toute technique de moissonnage pour collecter les données du site.")
    )
    assert result.status == "yes"
    assert result.sentences[0][1] == "French"


def test_terms_spanish():
    result = assess_terms(terms_page("Queda prohibido el uso de robots, arañas o cualquier medio automatizado para extraer contenido."))
    assert result.status == "yes"
    assert result.sentences[0][1] == "Spanish"


def test_terms_german():
    result = assess_terms(terms_page("Die Nutzung von Robotern, Crawlern oder automatisierten Abrufprogrammen ist untersagt."))
    assert result.status == "yes"
    assert result.sentences[0][1] == "German"


def test_terms_dutch():
    result = assess_terms(terms_page("Het gebruik van robots of geautomatiseerde systemen is niet toegestaan."))
    assert result.status == "yes"
    assert result.sentences[0][1] == "Dutch"


def test_terms_without_mention_is_no():
    assert assess_terms(terms_page("Both parties agree to the terms at the bottom of this page.")).status == "no"


def test_short_terms_page_is_failed_not_no():
    result = assess_terms("<html><body><div id='root'></div><script>app()</script></body></html>")
    assert result.status.startswith("failed: terms page has only")


def test_only_two_sentences_shown_but_total_counted():
    sentences = " ".join(f"Robots are banned on rule {i}." for i in range(4))
    result = assess_terms(terms_page(sentences))
    assert len(result.sentences) == 2
    assert result.total_matches == 4


def test_guess_language_unknown_when_unclear():
    assert guess_language("xyz qrs") == "unknown"
