"""End-to-end tests over the synthetic scenarios (T5.3). Offline: fake transport, fake clock.

Three views of the same 35 scenarios:
  1. each one through triage_firm, checked against the expectation written from the spec;
  2. all of them through run_triage: the JSONL file, resume, and the gate summary;
  3. each one through the command-line tool, live and then replayed from the cache it wrote.
"""

import dataclasses
import json
from urllib.parse import urlsplit

import pytest

from fake_transport import FakeClock, FakeTransport
from scenario_loader import build_transport, load_scenarios, mismatches
from tracker.triage.cli import main
from tracker.triage.contract import TriageResult
from tracker.triage.fetcher import Fetcher
from tracker.triage.orchestrator import triage_firm
from tracker.triage.runner import format_summary, needs_rerun, run_triage

AGENT = "InternshipTracker/0.1 (triage; contact test@example.test)"
SCENARIOS = load_scenarios()
IDS = [scenario.name for scenario in SCENARIOS]


def run_scenario(scenario, firm=None):
    """(result, transport) for one scenario, through a real Fetcher on the scripted transport."""
    clock = FakeClock()
    transport = build_transport(scenario, clock)
    fetcher = Fetcher(transport, AGENT, sleep=clock.sleep, clock=clock)
    return triage_firm(firm or scenario.firm, fetcher, clock=clock), transport


# -- 1. each scenario through triage_firm ------------------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_the_scenario_gives_the_result_the_spec_says(scenario):
    result, _ = run_scenario(scenario)
    assert mismatches(result, scenario.expected) == [], scenario.description
    assert TriageResult.from_json(result.to_json()) == result


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_the_result_lists_exactly_what_went_over_the_wire(scenario):
    result, transport = run_scenario(scenario)
    assert list(result.requests) == transport.calls


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_every_request_in_every_scenario_is_polite(scenario):
    result, transport = run_scenario(scenario)
    first_seen, last_time = {}, {}
    for url, sent, started, timeout in zip(transport.calls, transport.sent_headers, transport.times, transport.timeouts):
        host = urlsplit(url).hostname
        assert "InternshipTracker" in sent["User-Agent"] and "test@example.test" in sent["User-Agent"]
        assert timeout == 15
        assert not {"linkedin", "indeed"} & set(host.split("."))
        if host not in first_seen:
            first_seen[host] = url
        if host in last_time:
            assert started - last_time[host] >= 1.0, f"two requests to {host} less than 1 s apart"
        last_time[host] = started
    for host, url in first_seen.items():
        assert url.endswith("/robots.txt"), f"{host}: the first request was {url}, not its robots.txt"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_a_failed_or_blocked_result_never_claims_to_be_manual_or_empty(scenario):
    result, _ = run_scenario(scenario)
    if result.status in ("failed", "blocked"):
        assert result.verdict in ("unresolved", "blocked") and result.mode in ("unknown", "blocked")
    assert result.status != "empty"


# -- 2. all scenarios through the runner ----------------------------------------------------------------------------------------


def named(scenario):
    """The scenario's firm under the scenario's own name, so a run can hold all of them at once."""
    return dataclasses.replace(scenario.firm, name=scenario.name)


def batch_triage():
    by_name = {scenario.name: scenario for scenario in SCENARIOS}
    calls = []

    def triage(firm):
        calls.append(firm.name)
        return run_scenario(by_name[firm.name], firm)[0]

    return triage, calls


def test_all_scenarios_through_the_runner_write_one_correct_line_each(tmp_path):
    triage, calls = batch_triage()
    out = tmp_path / "out.jsonl"
    report = run_triage([named(s) for s in SCENARIOS], triage, out)
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").split("\n") if line]
    assert [line["firm"]["name"] for line in lines] == IDS == calls
    for scenario, result in zip(SCENARIOS, report.results):
        assert mismatches(result, scenario.expected) == [], scenario.name
    assert report.warnings == ()


def test_resume_over_all_scenarios_asks_again_only_for_retryable_failures(tmp_path):
    triage, _ = batch_triage()
    out = tmp_path / "out.jsonl"
    first = run_triage([named(s) for s in SCENARIOS], triage, out)
    retryable = [result.firm.name for result in first.results if needs_rerun(result)]
    assert retryable                                         # the timeout, 5xx and crawl-delay scenarios
    triage_again, calls = batch_triage()
    second = run_triage([named(s) for s in SCENARIOS], triage_again, out, resume=True)
    assert sorted(calls) == sorted(retryable)
    assert second.resumed == len(SCENARIOS) - len(retryable)
    assert [r.verdict for r in second.results] == [r.verdict for r in first.results]


def test_the_summary_counts_match_the_verdicts_the_scenarios_expect(tmp_path):
    triage, _ = batch_triage()
    report = run_triage([named(s) for s in SCENARIOS], triage, tmp_path / "out.jsonl")
    counts = {verdict: sum(1 for s in SCENARIOS if s.expected["verdict"] == verdict)
              for verdict in ("automatic", "probable", "manual", "blocked", "unresolved")}
    summary = format_summary(report.results)
    assert f"{len(SCENARIOS)} firm(s)" in summary
    assert "automatic {automatic}, probable {probable}, manual {manual}, blocked {blocked}, unresolved {unresolved}".format(**counts) in summary
    assert "Gate (threshold 8):" in summary and "Blocked:" in summary and "Unresolved:" in summary


# -- 3. each scenario through the command-line tool: live, then replay -----------------------------------------------------------


def write_firm_list(path, firm):
    cells = [firm.name, firm.careers_url, firm.listing_url or "", firm.example_job_url or "", firm.terms_url or "", firm.api_url or ""]
    path.write_text("firm;careers_entry_url;listing_url;example_job_url;terms_url;api_url\n" + ";".join(cells) + "\n", encoding="utf-8")


def stored(path):
    [line] = [json.loads(text) for text in path.read_text(encoding="utf-8").split("\n") if text]
    line.pop("checked_at")
    return line


@pytest.mark.parametrize("scenario", SCENARIOS, ids=IDS)
def test_a_live_run_then_a_replay_gives_the_same_result_without_touching_the_network(scenario, tmp_path, capsys):
    firms, cache = tmp_path / "firms.csv", tmp_path / "cache"
    write_firm_list(firms, scenario.firm)
    clock = FakeClock()
    live_code = main(["--input", str(firms), "--output", str(tmp_path / "live.jsonl"), "--cache-dir", str(cache)],
                     environ={"SURVEY_CONTACT": "test@example.test"}, live_transport=build_transport(scenario, clock),
                     sleep=clock.sleep, clock=clock)
    assert live_code == 0
    cache.mkdir(exist_ok=True)                               # a scenario with no request caches nothing

    def no_sleep(seconds):
        raise AssertionError("replay must never wait")

    spy = FakeTransport()
    replay_code = main(["--input", str(firms), "--output", str(tmp_path / "replay.jsonl"), "--cache-dir", str(cache), "--replay"],
                       environ={}, live_transport=spy, sleep=no_sleep)
    assert replay_code == 0 and spy.calls == []
    live, replayed = stored(tmp_path / "live.jsonl"), stored(tmp_path / "replay.jsonl")
    assert live == replayed
    result = TriageResult.from_dict({**live, "checked_at": "2026-11-16T10:00:00+00:00"})
    assert mismatches(result, scenario.expected) == []
