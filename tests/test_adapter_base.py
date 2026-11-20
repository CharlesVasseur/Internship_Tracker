"""Offline tests for the adapter result contract."""

from tracker.adapters.base import (
    AdapterResult,
    Listing,
    validate_result,
)


def test_accepts_all_valid_statuses():
    listing = Listing("job-1", "Analyst Intern", "Zurich", None, None, None, "en")
    assert validate_result(AdapterResult("ok", (listing,), ("Evidence",), None, 200)) == []
    assert validate_result(AdapterResult("empty", (), ("No rows",), None, 200)) == []
    assert validate_result(AdapterResult("failed", (), (), "timeout", 504)) == []
    assert validate_result(AdapterResult("blocked", (), (), "forbidden", 403)) == []


def test_reports_every_contract_problem():
    listing = Listing("job-1", "   ", None, None, None, None, None)
    result = AdapterResult("weird", (listing,), (), "unexpected", None)

    problems = validate_result(result)

    assert "invalid status: weird" in problems
    assert "listing 0 title must be non-empty" in problems

    ok_problems = validate_result(AdapterResult("ok", (), (), "error"))
    assert "ok status requires at least 1 listing" in ok_problems
    assert "ok status must not have an error" in ok_problems

    failed_problems = validate_result(AdapterResult("failed"))
    assert "failed status requires a non-empty error" in failed_problems
