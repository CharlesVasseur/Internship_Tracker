"""Tests for the scenario fixtures and their loader (T5.2). The scenarios are RUN in T5.3.

These tests check that the fixture files are well-formed and firm-neutral, and that the loader and
the expectation checker do what they say. No network, no firm-specific data.
"""

import json
import re
from urllib.parse import urlsplit

import pytest

from fake_transport import FakeTransport
from scenario_loader import (EXPECTED_KEYS, RESPONSE_KEYS, SCENARIOS_DIR, Scenario, build_transport,
                             load_scenarios, mismatches)
from tracker.triage.contract import (BLOCK_REASONS, ERROR_KINDS, EVIDENCE_KINDS, MODES, STATUSES,
                                     TERMS_STATUSES, VERDICTS, BlockInfo, Evidence, FirmInput, TermsFlag,
                                     TriageError, TriageResult)
from tracker.triage.transport import RawResponse, TransportError

SCENARIOS = load_scenarios()
IDS = [scenario.name for scenario in SCENARIOS]
GENERIC_HOSTS = {"schema.org", "www.sitemaps.org", "acme.myworkdayjobs.com", "www.linkedin.com", "www.w3.org"}


def hosts_in(text):
    return {urlsplit(url).hostname for url in re.findall(r"https?://[^\s\"'<>)]+", text)}


def is_allowed_host(host):
    return host is None or host == "example.test" or host.endswith(".example.test") or host in GENERIC_HOSTS


# -- the folder as a whole --------------------------------------------------------------------------------------------


def test_there_are_enough_scenarios_with_unique_names():
    assert len(SCENARIOS) >= 30 and len(set(IDS)) == len(IDS)


def test_every_folder_has_a_scenario_file_and_nothing_else_is_missing():
    folders = [path for path in SCENARIOS_DIR.iterdir() if path.is_dir()]
    assert len(folders) == len(SCENARIOS)
    assert not [path for path in SCENARIOS_DIR.iterdir() if path.is_file() and path.name != "README.md"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_the_scenario_file_has_exactly_the_expected_parts(scenario):
    document = json.loads((scenario.folder / "scenario.json").read_text(encoding="utf-8"))
    assert set(document) == {"description", "firm", "responses", "expected"}
    assert document["description"].strip().endswith(".")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_every_file_is_used_and_every_referenced_file_exists(scenario):
    specs = [item for spec in scenario.responses.values() for item in (spec if isinstance(spec, list) else [spec])]
    used = {item["file"] for item in specs if "file" in item}
    present = {path.name for path in scenario.folder.iterdir() if path.name != "scenario.json"}
    assert used == present, f"unused: {sorted(present - used)}, missing: {sorted(used - present)}"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_response_objects_use_only_known_keys(scenario):
    for spec in scenario.responses.values():
        for item in spec if isinstance(spec, list) else [spec]:
            assert set(item) <= RESPONSE_KEYS


# -- firm-neutral ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_the_firm_is_the_example_firm(scenario):
    assert scenario.firm.name == "Example Firm"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_every_address_is_on_an_example_host(scenario):
    text = (scenario.folder / "scenario.json").read_text(encoding="utf-8")
    assert all(is_allowed_host(host) for host in hosts_in(text)), hosts_in(text)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_page_files_only_point_to_example_or_generic_hosts(scenario):
    for path in scenario.folder.iterdir():
        text = path.read_bytes().decode("utf-8", errors="replace")
        assert all(is_allowed_host(host) for host in hosts_in(text)), (path.name, hosts_in(text))


def test_only_the_policy_scenario_uses_a_linkedin_address():
    users = [s.name for s in SCENARIOS if "linkedin" in s.firm.careers_url.lower()]
    assert users == ["policy_linkedin"]


# -- the expectations are valid and say something ------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_expected_values_belong_to_the_vocabulary(scenario):
    e = scenario.expected
    assert set(e) <= EXPECTED_KEYS and {"status", "mode", "verdict"} <= set(e)
    assert e["status"] in STATUSES and e["mode"] in MODES and e["verdict"] in VERDICTS
    assert e.get("error_kind", "internal") in ERROR_KINDS
    assert e.get("block_reason", "http_403") in BLOCK_REASONS
    assert e.get("terms_status", "flagged") in TERMS_STATUSES
    assert set(e.get("evidence_kinds_include", []) + e.get("evidence_kinds_exclude", [])) <= EVIDENCE_KINDS


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_a_failed_or_blocked_expectation_names_its_cause(scenario):
    e = scenario.expected
    assert (e["status"] != "failed") or "error_kind" in e
    assert (e["status"] != "blocked") or "block_reason" in e


def test_the_scenarios_cover_every_verdict_and_every_mode_that_can_be_produced():
    verdicts = {s.expected["verdict"] for s in SCENARIOS}
    modes = {s.expected["mode"] for s in SCENARIOS}
    assert verdicts == set(VERDICTS)
    assert modes == set(MODES) - {"json-api"}               # json-api is reserved: nothing produces it


def test_the_scenarios_cover_the_main_failure_kinds_and_block_reasons():
    kinds = {s.expected.get("error_kind") for s in SCENARIOS} - {None}
    reasons = {s.expected.get("block_reason") for s in SCENARIOS} - {None}
    assert {"http_4xx", "http_5xx", "tls", "timeout", "invalid_url", "budget"} <= kinds
    assert {"http_403", "http_429", "robots_disallow"} <= reasons


def test_the_scenarios_cover_the_three_terms_outcomes():
    assert {s.expected.get("terms_status") for s in SCENARIOS} >= {"flagged", "not_flagged", "not_assessed"}


# -- the loader -------------------------------------------------------------------------------------------------------------


def scenario_with(tmp_path, responses, files=None):
    for name, content in (files or {}).items():
        (tmp_path / name).write_bytes(content)
    return Scenario("x", "d.", tmp_path, FirmInput("Example Firm", "https://a.example.test"), responses, {})


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_the_transport_serves_every_scripted_url(scenario):
    transport = build_transport(scenario)
    for url in scenario.responses:
        try:
            answer = transport(url, {}, 1.0)
        except TransportError:
            continue                                         # a scripted error is a valid answer
        assert isinstance(answer, RawResponse)


def test_an_unscripted_url_is_still_refused():
    with pytest.raises(BaseException, match="unexpected request"):
        build_transport(SCENARIOS[0])("https://nowhere.example.test/x", {}, 1.0)


def test_a_file_response_takes_its_content_type_from_the_extension(tmp_path):
    scenario = scenario_with(tmp_path, {"https://a.example.test/p": {"file": "p.xml"}}, {"p.xml": b"<a/>"})
    answer = build_transport(scenario)("https://a.example.test/p", {}, 1.0)
    assert (answer.status, answer.headers["content-type"], answer.body, answer.truncated) == (200, "application/xml", b"<a/>", False)


def test_a_content_type_status_and_truncation_can_be_overridden(tmp_path):
    spec = {"file": "p.xml", "content_type": "application/rss+xml", "status": 203, "truncated": True}
    answer = build_transport(scenario_with(tmp_path, {"https://a.example.test/p": spec}, {"p.xml": b"x"}))(
        "https://a.example.test/p", {}, 1.0)
    assert (answer.status, answer.headers["content-type"], answer.truncated) == (203, "application/rss+xml", True)


def test_an_inline_body_error_and_redirect_are_built(tmp_path):
    scenario = scenario_with(tmp_path, {
        "https://a.example.test/body": {"status": 404, "body": "gone"},
        "https://a.example.test/err": {"error": "timeout", "message": "slow"},
        "https://a.example.test/go": {"redirect": "https://a.example.test/new"},
    })
    transport = build_transport(scenario)
    body = transport("https://a.example.test/body", {}, 1.0)
    assert (body.status, body.body) == (404, b"gone")
    with pytest.raises(TransportError) as error:
        transport("https://a.example.test/err", {}, 1.0)
    assert error.value.kind == "timeout"
    move = transport("https://a.example.test/go", {}, 1.0)
    assert (move.status, move.headers["location"]) == (301, "https://a.example.test/new")


def test_a_list_of_responses_is_served_in_order_and_the_last_repeats(tmp_path):
    scenario = scenario_with(tmp_path, {"https://a.example.test/p": [{"status": 500}, {"status": 200, "body": "ok"}]})
    transport = build_transport(scenario)
    assert [transport("https://a.example.test/p", {}, 1.0).status for _ in range(3)] == [500, 200, 200]


def test_an_unknown_response_key_is_refused_with_the_scenario_name(tmp_path):
    scenario = scenario_with(tmp_path, {"https://a.example.test/p": {"flie": "p.xml"}})
    with pytest.raises(ValueError, match="x: unknown response key"):
        build_transport(scenario)


def test_load_scenarios_reads_a_folder(tmp_path):
    folder = tmp_path / "one"
    folder.mkdir()
    (folder / "scenario.json").write_text(json.dumps({
        "description": "A test.", "firm": {"name": "Example Firm", "careers_url": "https://a.example.test"},
        "responses": {}, "expected": {"status": "ok", "mode": "manual", "verdict": "manual"}}), encoding="utf-8")
    [loaded] = load_scenarios(tmp_path)
    assert (loaded.name, loaded.firm.careers_url) == ("one", "https://a.example.test")


# -- the expectation checker ---------------------------------------------------------------------------------------------------


def ok_result(**changes):
    note = Evidence("note", "https://a.example.test", "a person checks this firm by hand")
    values = dict(terms=TermsFlag(None, "not_assessed", why="none"), requests=("https://a.example.test/r",))
    values.update(changes)
    return TriageResult.ok(FirmInput("Example Firm", "https://a.example.test"), "manual", [note], **values)


BASE = {"status": "ok", "mode": "manual", "verdict": "manual"}


def test_a_matching_result_has_no_mismatch():
    assert mismatches(ok_result(), {**BASE, "terms_status": "not_assessed", "notes_include": ["by hand"],
                                    "evidence_kinds_include": ["note"], "requests_include": ["https://a.example.test/r"]}) == []


@pytest.mark.parametrize("wrong, fragment", [
    ({"status": "failed"}, "status"), ({"mode": "html"}, "mode"), ({"verdict": "probable"}, "verdict"),
    ({"terms_status": "flagged"}, "terms status"), ({"error_kind": "tls"}, "error kind"),
    ({"block_reason": "http_403"}, "block reason"), ({"retryable": True}, "retryable"),
    ({"evidence_kinds_include": ["robots"]}, "is missing"), ({"evidence_kinds_exclude": ["note"]}, "should not be present"),
    ({"notes_include": ["absent text"]}, "no note contains"), ({"requests_include": ["https://x.example.test"]}, "was not requested"),
    ({"requests_exclude": ["https://a.example.test/r"]}, "should not have been requested"), ({"no_requests": True}, "no request expected"),
])
def test_each_kind_of_mismatch_is_detected_and_named(wrong, fragment):
    problems = mismatches(ok_result(), {**BASE, **wrong})
    assert problems and fragment in " ".join(problems)


def test_error_and_block_expectations_read_the_result():
    firm = FirmInput("Example Firm", "https://a.example.test")
    failed = TriageResult.failed(firm, TriageError.of("timeout", "slow", "https://a.example.test"))
    blocked = TriageResult.blocked(firm, BlockInfo("http_403", "https://a.example.test", 403))
    assert mismatches(failed, {"status": "failed", "mode": "unknown", "verdict": "unresolved",
                               "error_kind": "timeout", "retryable": True}) == []
    assert mismatches(blocked, {"status": "blocked", "mode": "blocked", "verdict": "blocked", "block_reason": "http_403"}) == []
    assert mismatches(ok_result(), {**BASE, "error_kind": "timeout"})          # an ok result has no error
