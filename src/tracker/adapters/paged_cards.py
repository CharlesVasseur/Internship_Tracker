"""Check a paged job-card list through its verified JSON load-more endpoint."""

from tracker.adapters.base import AdapterResult, Listing
from tracker.adapters.job_cards import (
    Card,
    parse_cards,
    parse_jobs_api,
    parse_total_count,
)
from tracker.triage.fetcher import Fetcher


ADAPTER_NAME = "paged_cards"


def _fetch_error(fetch_result) -> AdapterResult | None:
    if fetch_result.status == "blocked":
        block = fetch_result.block
        reason = block.reason if block is not None else "unknown"
        return AdapterResult(
            "blocked",
            error=f"blocked: {reason} at {fetch_result.url}",
            http_status=fetch_result.http_status,
        )
    if fetch_result.status == "failed":
        error = fetch_result.error
        if error is None:
            return AdapterResult(
                "failed",
                error=f"internal: no error details at {fetch_result.url}",
                http_status=fetch_result.http_status,
            )
        return AdapterResult(
            "failed",
            error=f"{error.kind}: {error.message}",
            http_status=fetch_result.http_status,
        )
    return None


def _listing(card: Card) -> Listing:
    description_parts = [
        value
        for value in (card.category, card.contract_type, card.employment_type)
        if value
    ]
    return Listing(
        source_job_id=card.source_job_id,
        title=card.title,
        location_raw=card.city,
        url=card.url,
        posted_date=None,
        description=" | ".join(description_parts) or None,
        language=None,
    )


def check_paged_cards(fetcher: Fetcher, config: dict) -> AdapterResult:
    """Fetch and validate every page of a configured card list."""
    list_url = config["list_url"]
    api_template = config["api_url_template"]
    first_api_page = config.get("first_api_page", 2)
    max_pages = config.get("max_pages", 30)

    first = fetcher.fetch(list_url)
    fetch_error = _fetch_error(first)
    if fetch_error is not None:
        return fetch_error

    page_text = first.text
    if page_text is None:
        return AdapterResult("failed", error="unexpected page layout: no job list found")
    parsed = parse_cards(page_text)
    announced = parse_total_count(page_text)
    if announced is None or (not parsed.cards and not parsed.has_container):
        return AdapterResult("failed", error="unexpected page layout: no job list found")

    if announced == 0 and parsed.has_container and not parsed.cards:
        return AdapterResult(
            "empty",
            evidence=("pages=1", "announced=0"),
        )

    cards: list[Card] = list(parsed.cards)
    problems = list(parsed.problems)
    duplicates = parsed.duplicates
    seen_ids = {card.source_job_id for card in cards}
    pages = 1

    if len(cards) == announced and not problems:
        return AdapterResult(
            "ok",
            listings=tuple(_listing(card) for card in cards),
            evidence=(
                f"pages={pages}",
                f"cards={len(cards)}",
                f"announced={announced}",
                f"duplicates={duplicates}",
            ),
        )

    if len(cards) > announced:
        return AdapterResult(
            "failed",
            error=(
                f"incomplete or inconsistent list: collected {len(cards)} "
                f"unique cards, announced {announced}"
            ),
        )

    for page in range(first_api_page, first_api_page + max_pages):
        api_url = api_template.format(page=page)
        response = fetcher.fetch(api_url)
        fetch_error = _fetch_error(response)
        if fetch_error is not None:
            return fetch_error
        if response.text is None:
            return AdapterResult(
                "failed",
                error="unexpected API response: missing response text",
            )
        try:
            api_page = parse_jobs_api(response.text)
        except ValueError as error:
            return AdapterResult("failed", error=str(error))

        pages += 1
        problems.extend(api_page.problems)
        duplicates += api_page.duplicates
        for card in api_page.cards:
            if card.source_job_id in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(card.source_job_id)
            cards.append(card)

        if not api_page.cards and not api_page.nothing_more:
            return AdapterResult("failed", error="empty page without end marker")
        if api_page.nothing_more:
            break
    else:
        return AdapterResult(
            "failed",
            error=f"too many pages: no end marker after {max_pages} pages",
        )

    if problems or len(cards) != announced:
        error = (
            f"incomplete or inconsistent list: collected {len(cards)} unique cards, "
            f"announced {announced}"
        )
        if problems:
            error += "; " + "; ".join(problems)
        return AdapterResult("failed", error=error)

    return AdapterResult(
        "ok",
        listings=tuple(_listing(card) for card in cards),
        evidence=(
            f"pages={pages}",
            f"cards={len(cards)}",
            f"announced={announced}",
            f"duplicates={duplicates}",
        ),
    )
