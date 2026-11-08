"""T1 tests: the triage contract. Pure Python, no network, no files."""

import json

import pytest

from tracker.triage.contract import (
    ERROR_KINDS,
    EVIDENCE_KINDS,
    MAX_DETAIL,
    MODES,
    OK_MODES,
    REQUIRED_EVIDENCE,
    VERDICTS,
    BlockInfo,
    ContractError,
    Evidence,
    FirmInput,
    TermsFlag,
    TriageError,
    TriageResult,
    derive_verdict,
    gate_decision,
)

FIRM = FirmInput("Acme", "https://acme.test/careers")
NOW = "2026-11-08T12:00:00+00:00"
URL = "https://acme.test/x"


def ev(kind, detail="observed"):
    return Evidence(kind, URL, detail)


def ok_result(mode, *extra_kinds):
    """An ok result with the evidence its mode requires, plus extra kinds."""
    kinds = []
    if mode in REQUIRED_EVIDENCE:
        kinds.append(REQUIRED_EVIDENCE[mode])
    kinds.extend(extra_kinds)
    if not kinds:
        kinds = ["note"]
    return TriageResult.ok(FIRM, mode, [ev(k) for k in kinds], checked_at=NOW)


def failed_result(kind="timeout"):
    return TriageResult.failed(FIRM, TriageError.of(kind, "it broke", URL), checked_at=NOW)


def blocked_result(reason="http_403"):
    return TriageResult.blocked(FIRM, BlockInfo(reason, URL, 403), checked_at=NOW)


def empty_result():
    return TriageResult.empty(FIRM, checked_at=NOW)


# ---- Inputs are permissive ----------------------------------------------------


def test_firm_input_never_validates_so_bad_rows_become_results_later():
    FirmInput("", "not a url")  # must not raise
    FirmInput("Acme", "", listing_url="???")


# ---- Evidence, errors, blocks -------------------------------------------------


def test_unknown_evidence_kind_is_refused():
    with pytest.raises(ContractError):
        Evidence("guess", URL, "x")


def test_long_evidence_is_refused_by_the_constructor_and_cut_by_of():
    with pytest.raises(ContractError):
        Evidence("note", URL, "x" * (MAX_DETAIL + 1))
    cut = Evidence.of("note", URL, "x" * 1000)
    assert len(cut.detail) == MAX_DETAIL and cut.detail.endswith("[...cut]")


def test_error_retryable_defaults_follow_the_kind():
    assert TriageError.of("timeout", "slow").retryable is True
    assert TriageError.of("tls", "certificate verify failed").retryable is False
    assert TriageError.of("decode", "unknown encoding").retryable is False
    assert TriageError.of("internal", "bug").retryable is False
    assert TriageError.of("timeout", "slow", retryable=False).retryable is False


def test_every_error_kind_can_be_built():
    for kind in ERROR_KINDS:
        assert TriageError.of(kind, "message").kind == kind


def test_error_needs_a_known_kind_and_a_message():
    with pytest.raises(ContractError):
        TriageError.of("mystery", "x")
    with pytest.raises(ContractError):
        TriageError("timeout", "   ", None, True)


def test_unknown_block_reason_is_refused():
    with pytest.raises(ContractError):
        BlockInfo("rude", URL)


# ---- Result invariants ----------------------------------------------------------


def test_failed_needs_an_error_and_mode_unknown():
    with pytest.raises(ContractError):
        TriageResult(FIRM, "failed", "unknown", NOW)  # no error
    with pytest.raises(ContractError):
        TriageResult(FIRM, "failed", "html", NOW, error=TriageError.of("timeout", "x"))
    assert failed_result().mode == "unknown"


def test_failed_cannot_also_be_blocked():
    with pytest.raises(ContractError):
        TriageResult(FIRM, "failed", "unknown", NOW,
                     error=TriageError.of("timeout", "x"), block=BlockInfo("http_403", URL))


def test_blocked_needs_block_info_and_mode_blocked():
    with pytest.raises(ContractError):
        TriageResult(FIRM, "blocked", "blocked", NOW)  # no block
    with pytest.raises(ContractError):
        TriageResult(FIRM, "blocked", "unknown", NOW, block=BlockInfo("http_403", URL))
    assert blocked_result().mode == "blocked"


def test_blocked_cannot_carry_an_error():
    with pytest.raises(ContractError):
        TriageResult(FIRM, "blocked", "blocked", NOW,
                     block=BlockInfo("http_403", URL), error=TriageError.of("timeout", "x"))


def test_empty_has_mode_unknown_and_no_error_or_block():
    assert empty_result().mode == "unknown"
    with pytest.raises(ContractError):
        TriageResult(FIRM, "empty", "manual", NOW)
    with pytest.raises(ContractError):
        TriageResult(FIRM, "empty", "unknown", NOW, error=TriageError.of("timeout", "x"))


def test_ok_needs_an_ok_mode_and_evidence():
    with pytest.raises(ContractError):
        TriageResult(FIRM, "ok", "unknown", NOW, evidence=(ev("note"),))
    with pytest.raises(ContractError):
        TriageResult(FIRM, "ok", "blocked", NOW, evidence=(ev("note"),))
    with pytest.raises(ContractError):
        TriageResult.ok(FIRM, "manual", [], checked_at=NOW)  # no evidence


@pytest.mark.parametrize("mode", sorted(REQUIRED_EVIDENCE))
def test_each_mode_demands_its_own_kind_of_evidence(mode):
    with pytest.raises(ContractError):
        TriageResult.ok(FIRM, mode, [ev("note")], checked_at=NOW)
    ok_result(mode)  # builds with the right evidence


def test_ok_cannot_carry_an_error_or_a_block():
    with pytest.raises(ContractError):
        TriageResult(FIRM, "ok", "manual", NOW, evidence=(ev("note"),),
                     error=TriageError.of("timeout", "x"))
    with pytest.raises(ContractError):
        TriageResult(FIRM, "ok", "manual", NOW, evidence=(ev("note"),),
                     block=BlockInfo("http_403", URL))


def test_unknown_status_mode_and_bad_timestamp_are_refused():
    with pytest.raises(ContractError):
        TriageResult(FIRM, "maybe", "unknown", NOW)
    with pytest.raises(ContractError):
        TriageResult(FIRM, "ok", "telepathy", NOW, evidence=(ev("note"),))
    with pytest.raises(ContractError):
        TriageResult.empty(FIRM, checked_at="yesterday")


def test_evidence_must_be_a_tuple_of_evidence():
    with pytest.raises(ContractError):
        TriageResult(FIRM, "ok", "manual", NOW, evidence=[ev("note")])  # a list, not a tuple
    with pytest.raises(ContractError):
        TriageResult(FIRM, "ok", "manual", NOW, evidence=("text",))


def test_failed_blocked_and_empty_may_keep_partial_evidence():
    result = TriageResult.failed(
        FIRM, TriageError.of("timeout", "x"), [ev("robots", "Crawl-delay: 5")], checked_at=NOW
    )
    assert result.evidence[0].kind == "robots"


# ---- Verdict --------------------------------------------------------------------


def test_verdict_table():
    assert derive_verdict(ok_result("feed")) == "automatic"
    assert derive_verdict(ok_result("json-ld", "job_links")) == "automatic"
    assert derive_verdict(ok_result("json-ld", "sitemap_jobs")) == "automatic"
    assert derive_verdict(ok_result("json-ld")) == "probable"
    assert derive_verdict(ok_result("json-ld", "sitemap_line")) == "probable"  # line only, contents unchecked
    assert derive_verdict(ok_result("vendor")) == "probable"
    assert derive_verdict(ok_result("html")) == "probable"
    assert derive_verdict(ok_result("json-api")) == "probable"
    assert derive_verdict(ok_result("js-only")) == "manual"
    assert derive_verdict(ok_result("manual")) == "manual"
    assert derive_verdict(blocked_result()) == "blocked"
    assert derive_verdict(failed_result()) == "unresolved"
    assert derive_verdict(empty_result()) == "unresolved"


def test_verdict_property_matches_the_function():
    result = ok_result("feed")
    assert result.verdict == derive_verdict(result)


def test_failed_and_blocked_are_never_manual_or_automatic():
    """A failed or blocked check must never read as an answer about the firm."""
    for kind in ERROR_KINDS:
        assert failed_result(kind).verdict == "unresolved"
    from tracker.triage.contract import BLOCK_REASONS
    for reason in BLOCK_REASONS:
        assert blocked_result(reason).verdict == "blocked"


def test_every_ok_mode_has_a_verdict():
    for mode in OK_MODES:
        assert ok_result(mode).verdict in VERDICTS


def test_policy_exclusion_is_a_determined_manual_result():
    """LinkedIn/Indeed are never fetched: ok, manual, with a policy evidence line."""
    result = TriageResult.ok(
        FIRM, "manual", [Evidence("policy", "https://www.linkedin.com/company/x", "host excluded by policy")],
        checked_at=NOW,
    )
    assert result.verdict == "manual"


# ---- Terms flag -----------------------------------------------------------------


def test_flagged_terms_need_one_or_two_quotes():
    with pytest.raises(ContractError):
        TermsFlag(URL, "flagged")
    with pytest.raises(ContractError):
        TermsFlag(URL, "flagged", quotes=("a", "b", "c"))
    assert TermsFlag(URL, "flagged", quotes=("a sentence",), language_guess="French").status == "flagged"


def test_not_flagged_cannot_carry_quotes_and_never_means_clearance():
    with pytest.raises(ContractError):
        TermsFlag(URL, "not_flagged", quotes=("x",))
    assert TermsFlag(URL, "not_flagged").status == "not_flagged"


def test_not_assessed_must_say_why():
    with pytest.raises(ContractError):
        TermsFlag(URL, "not_assessed")
    assert TermsFlag(URL, "not_assessed", why="PDF").why == "PDF"


def test_terms_status_vocabulary_has_no_plain_no():
    with pytest.raises(ContractError):
        TermsFlag(URL, "no")


def test_a_terms_flag_does_not_change_the_verdict():
    """Item 10 is a human judgment: the flag is shown, not applied."""
    flagged = TermsFlag(URL, "flagged", quotes=("robots are banned",))
    plain = ok_result("feed")
    with_terms = TriageResult.ok(FIRM, "feed", plain.evidence, terms=flagged, checked_at=NOW)
    assert with_terms.verdict == plain.verdict == "automatic"


# ---- Serialisation ----------------------------------------------------------------


def full_result():
    return TriageResult.ok(
        FirmInput("Crédit Agricole", "https://x.test/c", listing_url="https://x.test/l"),
        "json-ld",
        [Evidence("jsonld_jobposting", URL, "title: Stagiaire"), Evidence("job_links", URL, "32 links")],
        terms=TermsFlag(URL, "flagged", quotes=("Les robots sont interdits.",), language_guess="French"),
        requests=("https://x.test/robots.txt", "https://x.test/c"),
        checked_at=NOW,
    )


def test_json_round_trip_for_every_status():
    for result in (full_result(), failed_result(), blocked_result(), empty_result(), ok_result("manual")):
        again = TriageResult.from_json(result.to_json())
        assert again == result


def test_json_line_is_one_line_and_keeps_accents_and_verdict():
    line = full_result().to_json()
    assert "\n" not in line
    assert "Crédit Agricole" in line
    assert json.loads(line)["verdict"] == "automatic"


def test_stored_verdict_is_ignored_and_recomputed():
    data = full_result().to_dict()
    data["verdict"] = "blocked"  # a wrong stored value
    assert TriageResult.from_dict(data).verdict == "automatic"


def test_from_dict_refuses_broken_data_with_a_contract_error():
    with pytest.raises(ContractError):
        TriageResult.from_dict({"status": "ok"})
    with pytest.raises(ContractError):
        TriageResult.from_json("{not json")
    bad = failed_result().to_dict()
    bad["error"] = None  # a failed result without an error
    with pytest.raises(ContractError):
        TriageResult.from_dict(bad)


# ---- Gate ----------------------------------------------------------------------------


def firms(**counts):
    """A list of results with the given number of each verdict."""
    makers = {
        "automatic": lambda: ok_result("feed"),
        "probable": lambda: ok_result("vendor"),
        "manual": lambda: ok_result("manual"),
        "blocked": blocked_result,
        "unresolved": failed_result,
    }
    return [makers[verdict]() for verdict, n in counts.items() for _ in range(n)]


def test_gate_pass_at_threshold():
    report = gate_decision(firms(automatic=8, manual=11))
    assert report.decision == "PASS"


def test_gate_undecided_example_from_the_contract():
    """5 automatic, 4 probable, 3 unresolved, 4 manual, 4 blocked."""
    report = gate_decision(firms(automatic=5, probable=4, unresolved=3, manual=4, blocked=4))
    assert report.decision == "UNDECIDED"
    assert report.total == 20
    assert report.counts == {"automatic": 5, "probable": 4, "manual": 4, "blocked": 4, "unresolved": 3}


def test_gate_fails_only_when_even_the_best_case_misses():
    report = gate_decision(firms(automatic=3, probable=2, unresolved=1, manual=6, blocked=7))
    assert report.decision == "FAIL"  # 3+2+1 = 6 < 8


def test_gate_is_undecided_exactly_at_the_best_case_boundary():
    assert gate_decision(firms(automatic=3, probable=5, manual=10)).decision == "UNDECIDED"  # 8 possible
    assert gate_decision(firms(automatic=3, probable=4, manual=11)).decision == "FAIL"       # 7 possible


def test_blocked_and_manual_never_rescue_the_gate():
    assert gate_decision(firms(automatic=2, manual=9, blocked=8)).decision == "FAIL"


def test_gate_with_the_survey_firm_count_and_a_custom_threshold():
    report = gate_decision(firms(automatic=7, probable=8, blocked=2, unresolved=2), threshold=8)
    assert report.decision == "UNDECIDED"
    assert gate_decision(firms(automatic=7, probable=8, blocked=2, unresolved=2), threshold=7).decision == "PASS"


def test_gate_refuses_empty_input_and_bad_threshold():
    with pytest.raises(ContractError):
        gate_decision([])
    with pytest.raises(ContractError):
        gate_decision(firms(automatic=1), threshold=0)


def test_gate_explanation_mentions_the_numbers():
    report = gate_decision(firms(automatic=5, probable=4, unresolved=3, manual=4, blocked=4))
    assert "5 automatic of 20" in report.explanation
    assert "12" in report.explanation


# ---- Vocabulary sanity ------------------------------------------------------------------


def test_every_required_evidence_kind_exists_and_every_mode_is_listed():
    for kind in REQUIRED_EVIDENCE.values():
        assert kind in EVIDENCE_KINDS
    assert set(OK_MODES) <= set(MODES)
    assert {"blocked", "unknown"} <= set(MODES)
