"""Offline tests for the load-more job-card parser."""

import json
import pytest

from tracker.adapters.job_cards import parse_cards, parse_jobs_api, parse_total_count


def _card(
    card_id: str,
    title: str = "Example Intern",
    city: str = "Example City",
    contract: str = "Example Contract",
    employment: str = "Example Time",
    category: str = "Example Category",
    with_link: bool = True,
) -> str:
    link = (
        f'<div class="job-card-overlay">'
        f'<a href="https://jobs.example.test/fr/open/{card_id}" '
        f'aria-label="{title}"></a></div>'
        if with_link
        else ""
    )
    city_html = (
        f'<div class="icon city"><span class="pin">{city}</span></div>'
        if city
        else ""
    )
    contract_html = (
        f'<div class="icon contract-type">'
        f'<span class="contract-type">{contract}</span></div>'
        if contract
        else ""
    )
    employment_html = (
        f'<div class="icon employment-type">'
        f'<span class="timer">{employment}</span></div>'
        if employment
        else ""
    )
    return (
        '<div class="cards-container">'
        f'<div class="Panel job-card GridFlex-col-12" id="{card_id}">'
        f"{link}"
        f'<div class="job-card-body">'
        f'<div class="job-card-title"> {title} </div>'
        f"</div>"
        f'<div class="job-card-footer">'
        f'<span class="Badge is-info">{category}</span>'
        f'<div class="icons">{city_html}{contract_html}{employment_html}'
        f'<span class="Badge is-info is-inverted">Second Category</span>'
        f"</div></div></div></div>"
    )


def test_two_complete_cards_keep_fields_entities_and_order():
    html = (
        '<div class="cards-container">'
        '<div class="job-card" id="card-a">'
        '<div class="job-card-overlay"><a href="https://jobs.example.test/a" '
        'aria-label="Example Intern A"></a></div>'
        '<div class="job-card-title">  Example Intern A &amp; B (F/M/X)  </div>'
        '<div class="icon city"><span>  Example City A  </span></div>'
        '<div class="icon contract-type"><span>Example Contract A</span></div>'
        '<div class="icon employment-type"><span> Example Time A </span></div>'
        '<span class="Badge is-info">Example Category A</span>'
        "</div>"
        '<div class="job-card" id="card-b">'
        '<div class="job-card-overlay"><a href="https://jobs.example.test/b" '
        'aria-label="Example Intern B"></a></div>'
        '<div class="job-card-title">Example Intern B</div>'
        '<div class="icon city"><span>Example City B</span></div>'
        '<div class="icon contract-type"><span>Example Contract B</span></div>'
        '<div class="icon employment-type"><span>Example Time B</span></div>'
        '<span class="Badge is-info">Example Category B</span>'
        "<span class=\"Badge\">Second Badge</span>"
        "</div></div>"
    )

    result = parse_cards(html)

    assert len(result.cards) == 2
    assert result.cards[0].title == "Example Intern A & B (F/M/X)"
    assert result.cards[0].city == "Example City A"
    assert result.cards[0].contract_type == "Example Contract A"
    assert result.cards[0].employment_type == "Example Time A"
    assert result.cards[0].category == "Example Category A"
    assert result.cards[1].source_job_id == "card-b"
    assert result.cards[1].category == "Example Category B"
    assert [card.source_job_id for card in result.cards] == ["card-a", "card-b"]


def test_missing_optional_fields_and_first_badge():
    html = _card(
        "card-1",
        city="",
        contract="",
        employment="",
        category="First Badge",
    )

    result = parse_cards(html)

    assert len(result.cards) == 1
    assert result.cards[0].city is None
    assert result.cards[0].contract_type is None
    assert result.cards[0].employment_type is None
    assert result.cards[0].category == "First Badge"


def test_job_card_body_and_title_tokens_are_not_cards():
    html = (
        '<div class="job-card-body"><span>Example</span></div>'
        '<div class="job-card-title">Example Title</div>'
    )

    result = parse_cards(html)

    assert result.cards == ()
    assert result.problems == ()


def test_title_falls_back_to_overlay_aria_label():
    html = (
        '<div class="job-card" id="card-fallback">'
        '<div class="job-card-overlay"><a href="https://jobs.example.test/fallback" '
        'aria-label="  Example Fallback &amp; Title  "></a></div>'
        '<div class="job-card-title">   </div></div>'
    )

    result = parse_cards(html)

    assert len(result.cards) == 1
    assert result.cards[0].title == "Example Fallback & Title"


def test_card_without_title_is_skipped_with_position():
    html = (
        '<div class="job-card" id="card-1">'
        '<div class="job-card-overlay"><a href="https://jobs.example.test/1"></a></div>'
        '<div class="job-card-title"> </div></div>'
        '<div class="job-card" id="card-2">'
        '<div class="job-card-overlay"><a href="https://jobs.example.test/2"></a></div>'
        '<div class="job-card-title"> </div></div>'
    )

    result = parse_cards(html)

    assert result.cards == ()
    assert result.problems == (
        "card 1 skipped: missing title",
        "card 2 skipped: missing title",
    )


def test_missing_id_and_missing_link_skip_only_bad_cards():
    html = (
        '<div class="job-card">'
        '<div class="job-card-overlay"><a href="https://jobs.example.test/bad-id"></a></div>'
        '<div class="job-card-title">Bad Id</div></div>'
        '<div class="job-card" id="card-bad-link">'
        '<div class="job-card-overlay"></div>'
        '<div class="job-card-title">Bad Link</div></div>'
        '<div class="job-card" id="card-good">'
        '<div class="job-card-overlay"><a href="https://jobs.example.test/good"></a></div>'
        '<div class="job-card-title">Example Good</div></div>'
    )

    result = parse_cards(html)

    assert [card.source_job_id for card in result.cards] == ["card-good"]
    assert result.ignored == 1
    assert result.problems == ("card 2 skipped: missing link",)


def test_idless_card_is_ignored_and_api_page_reports_it():
    html = (
        '<div class="cards-container">'
        '<div class="job-card" id="card-a"><div class="job-card-overlay">'
        '<a href="https://jobs.example.test/a"></a></div>'
        '<div class="job-card-title">Example A</div></div>'
        '<div class="job-card" id="card-b"><div class="job-card-overlay">'
        '<a href="https://jobs.example.test/b"></a></div>'
        '<div class="job-card-title">Example B</div></div>'
        '<div class="job-card"><div class="job-card-overlay">'
        '<a href="https://jobs.example.test/no-id"></a></div>'
        '<div class="job-card-title">Ignored</div></div>'
        "</div>"
    )

    result = parse_cards(html)

    assert [card.source_job_id for card in result.cards] == ["card-a", "card-b"]
    assert result.ignored == 1
    assert result.problems == ()

    api_result = parse_jobs_api(
        json.dumps(
            {
                "html": html,
                "jobsCount": 3,
                "nothingMoreToDisplay": True,
            }
        )
    )
    assert api_result.ignored == 1


def test_duplicate_ids_keep_first_and_count_second():
    html = (
        '<div class="job-card" id="same-id">'
        '<div class="job-card-overlay"><a href="https://jobs.example.test/first"></a></div>'
        '<div class="job-card-title">Example First</div></div>'
        '<div class="job-card" id="same-id">'
        '<div class="job-card-overlay"><a href="https://jobs.example.test/second"></a></div>'
        '<div class="job-card-title">Example Second</div></div>'
    )

    result = parse_cards(html)

    assert len(result.cards) == 1
    assert result.cards[0].title == "Example First"
    assert result.duplicates == 1
    assert result.problems == ()


def test_wrappers_malformed_markup_and_stray_tags_are_tolerated():
    html = (
        "<main><section><div>"
        '<div class="job-card" id="unclosed">'
        '<div class="job-card-overlay"><a href="https://jobs.example.test/wrapped">'
        "</a></div><div class=\"job-card-title\">Example Wrapped</div>"
        "</unrelated>"
    )

    result = parse_cards(html)

    assert [card.source_job_id for card in result.cards] == ["unclosed"]
    assert result.problems == ()


def test_container_flags_and_empty_html():
    assert parse_cards('<div class="cards-container"></div>').has_container is True
    assert parse_cards('<div class="other-container"></div>').has_container is False
    empty = parse_cards("")
    assert empty.cards == ()
    assert empty.problems == ()
    assert empty.has_container is False


def test_parse_total_count():
    assert parse_total_count(
        '<div class="jobs-count"> 70  open positions </div>'
    ) == 70
    assert parse_total_count("<main>No count</main>") is None
    assert parse_total_count('<div class="jobs-count">open positions</div>') is None


def test_parse_jobs_api_valid_response():
    html = (
        '<div class="cards-container">'
        '<div class="job-card" id="api-a">'
        '<div class="job-card-overlay"><a href="https://jobs.example.test/a"></a></div>'
        '<div class="job-card-title">Example A</div></div>'
        '<div class="job-card" id="api-b">'
        '<div class="job-card-overlay"><a href="https://jobs.example.test/b"></a></div>'
        '<div class="job-card-title">Example B</div></div>'
        "</div>"
    )
    text = json.dumps(
        {
            "html": html,
            "jobsCount": 14,
            "nothingMoreToDisplay": True,
        }
    )

    result = parse_jobs_api(text)

    assert [card.source_job_id for card in result.cards] == ["api-a", "api-b"]
    assert result.jobs_count == 14
    assert result.nothing_more is True


def test_parse_jobs_api_empty_html():
    result = parse_jobs_api(
        json.dumps(
            {
                "html": "",
                "jobsCount": 0,
                "nothingMoreToDisplay": True,
            }
        )
    )

    assert result.cards == ()


@pytest.mark.parametrize(
    "text",
    [
        "{bad json",
        "[]",
        '{"html":"","jobsCount":0}',
        '{"html":"","jobsCount":"0","nothingMoreToDisplay":true}',
        '{"html":"","jobsCount":true,"nothingMoreToDisplay":true}',
        '{"html":"","jobsCount":0,"nothingMoreToDisplay":1}',
    ],
)
def test_parse_jobs_api_rejects_invalid_shapes(text):
    with pytest.raises(
        ValueError,
        match=r"^unexpected API response",
    ):
        parse_jobs_api(text)
