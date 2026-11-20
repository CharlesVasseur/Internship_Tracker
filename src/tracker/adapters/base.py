"""Typed contract for offline source adapters."""

from dataclasses import dataclass


RESULT_STATUSES = ("ok", "empty", "failed", "blocked")


@dataclass(frozen=True)
class Listing:
    """One listing returned by an adapter."""

    source_job_id: str | None
    title: str
    location_raw: str | None
    url: str | None
    posted_date: str | None
    description: str | None
    language: str | None


@dataclass(frozen=True)
class AdapterResult:
    """Typed outcome from a source adapter."""

    status: str
    listings: tuple[Listing, ...] = ()
    evidence: tuple[str, ...] = ()
    error: str | None = None
    http_status: int | None = None


def validate_result(result: AdapterResult) -> list[str]:
    """Return every contract problem in an adapter result."""
    problems: list[str] = []
    if result.status not in RESULT_STATUSES:
        problems.append(f"invalid status: {result.status}")

    if result.status == "ok":
        if not result.listings:
            problems.append("ok status requires at least 1 listing")
        if result.error is not None:
            problems.append("ok status must not have an error")
    elif result.status == "empty":
        if result.listings:
            problems.append("empty status requires 0 listings")
        if result.error is not None:
            problems.append("empty status must not have an error")
    elif result.status in ("failed", "blocked"):
        if result.error is None or not result.error.strip():
            problems.append(f"{result.status} status requires a non-empty error")
        if result.listings:
            problems.append(f"{result.status} status requires 0 listings")

    for index, listing in enumerate(result.listings):
        if not isinstance(listing.title, str) or not listing.title.strip():
            problems.append(f"listing {index} title must be non-empty")

    return problems
