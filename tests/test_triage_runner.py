"""Tests for run_triage and format_summary (T4.3). A stub replaces triage: no fetcher, no network."""

import json

import pytest

from tracker.triage.contract import BlockInfo, Evidence, FirmInput, TermsFlag, TriageError, TriageResult
from tracker.triage.runner import OutputExists, firm_key, format_summary, needs_rerun, read_results, run_triage


def firms(*names):
    return [FirmInput(name, f"https://{name.lower().replace(' ', '-')}.example.test/careers") for name in names]


def ok(firm, mode="html"):
    kind = {"html": "job_links", "feed": "feed_parsed"}[mode]
    return TriageResult.ok(firm, mode, [Evidence(kind, "https://x.test", "6 links")],
                           terms=TermsFlag(None, "not_assessed", why="no terms URL given"), requests=("https://x.test",))


def failed(firm, kind="timeout"):
    return TriageResult.failed(firm, TriageError.of(kind, "x", firm.careers_url))


def blocked(firm):
    return TriageResult.blocked(firm, BlockInfo("http_403", firm.careers_url, 403))


class Stub:
    """A triage stand-in: answers from a script (a result, or an exception to raise), else ok/html."""

    def __init__(self, script=None, default=ok):
        self.script = script or {}
        self.default = default
        self.calls = []

    def __call__(self, firm):
        self.calls.append(firm.name)
        action = self.script.get(firm.name, self.default)
        if isinstance(action, BaseException):
            raise action
        return action(firm) if callable(action) else action


def lines(path):
    return [line for line in path.read_text(encoding="utf-8").split("\n") if line]


def names_in(path):
    return [json.loads(line)["firm"]["name"] for line in lines(path)]


# -- writing ---------------------------------------------------------------------------------------


def test_one_json_line_per_firm_in_input_order(tmp_path):
    out = tmp_path / "out.jsonl"
    result = run_triage(firms("A", "B", "C"), Stub(), out)
    assert names_in(out) == ["A", "B", "C"]
    assert out.read_text(encoding="utf-8").endswith("\n")
    assert [r.firm.name for r in result.results] == ["A", "B", "C"]
    assert all(TriageResult.from_json(line) for line in lines(out))


def test_each_line_is_on_disk_before_the_next_firm_starts(tmp_path):
    out = tmp_path / "out.jsonl"
    seen = []

    def watch(firm):
        seen.append(len(lines(out)) if out.exists() else 0)
        return ok(firm)

    run_triage(firms("A", "B", "C"), watch, out)
    assert seen == [0, 1, 2]


def test_the_output_folder_is_created(tmp_path):
    out = tmp_path / "new" / "folder" / "out.jsonl"
    run_triage(firms("A"), Stub(), out)
    assert out.exists()


def test_accents_and_odd_characters_survive(tmp_path):
    out = tmp_path / "out.jsonl"
    odd = [FirmInput("Société \u2028 Générale", "https://x.example.test")]
    run_triage(odd, Stub(), out)
    assert "Société" in out.read_text(encoding="utf-8")        # written as text, not \u escapes
    again = run_triage(odd, Stub(), out, resume=True)
    assert again.resumed == 1                                   # the U+2028 did not cut the line in two


def test_progress_is_reported_per_firm(tmp_path):
    messages = []
    run_triage(firms("A", "B"), Stub({"B": failed}), tmp_path / "o.jsonl", report=messages.append)
    assert messages[0].startswith("[1/2] A: ok html probable")
    assert messages[1].startswith("[2/2] B: failed unknown unresolved [timeout]")


# -- the guard ----------------------------------------------------------------------------------------


def test_a_firm_that_raises_becomes_failed_internal_and_the_run_goes_on(tmp_path):
    out = tmp_path / "out.jsonl"
    stub = Stub({"B": RuntimeError("boom")})
    result = run_triage(firms("A", "B", "C"), stub, out)
    assert stub.calls == ["A", "B", "C"]
    middle = result.results[1]
    assert (middle.status, middle.error.kind, middle.verdict) == ("failed", "internal", "unresolved")
    assert "RuntimeError: boom" in middle.error.message
    assert names_in(out) == ["A", "B", "C"]


def test_a_triage_that_returns_something_else_is_failed_internal(tmp_path):
    result = run_triage(firms("A"), lambda firm: {"status": "ok"}, tmp_path / "o.jsonl")
    assert result.results[0].error.kind == "internal" and "TypeError" in result.results[0].error.message


def test_a_keyboard_interrupt_stops_the_run_but_keeps_finished_firms(tmp_path):
    out = tmp_path / "out.jsonl"
    with pytest.raises(KeyboardInterrupt):
        run_triage(firms("A", "B", "C", "D"), Stub({"C": KeyboardInterrupt()}), out)
    assert names_in(out) == ["A", "B"]
    second = Stub()
    final = run_triage(firms("A", "B", "C", "D"), second, out, resume=True)
    assert second.calls == ["C", "D"] and final.resumed == 2
    assert names_in(out) == ["A", "B", "C", "D"]


def test_a_write_problem_is_not_swallowed(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(OSError):                       # the "folder" is a file: loud, not silent
        run_triage(firms("A"), Stub(), blocker / "out.jsonl")


# -- an existing output file ---------------------------------------------------------------------------


def test_an_existing_output_is_never_overwritten_without_resume(tmp_path):
    out = tmp_path / "out.jsonl"
    run_triage(firms("A"), Stub(), out)
    before = out.read_bytes()
    stub = Stub()
    with pytest.raises(OutputExists, match="--resume"):
        run_triage(firms("A"), stub, out)
    assert out.read_bytes() == before and stub.calls == []


def test_an_empty_existing_file_is_fine_without_resume(tmp_path):
    out = tmp_path / "out.jsonl"
    out.write_text("")
    run_triage(firms("A"), Stub(), out)
    assert names_in(out) == ["A"]


def test_resume_on_a_missing_file_just_runs_everything(tmp_path):
    stub = Stub()
    result = run_triage(firms("A", "B"), stub, tmp_path / "out.jsonl", resume=True)
    assert stub.calls == ["A", "B"] and result.resumed == 0


# -- resume ------------------------------------------------------------------------------------------------


def test_resume_skips_finished_firms_and_appends_the_rest(tmp_path):
    out = tmp_path / "out.jsonl"
    run_triage(firms("A", "B"), Stub(), out)
    stub = Stub()
    messages = []
    result = run_triage(firms("A", "B", "C"), stub, out, resume=True, report=messages.append)
    assert stub.calls == ["C"] and result.resumed == 2
    assert names_in(out) == ["A", "B", "C"]
    assert "already done" in messages[0]
    assert [r.firm.name for r in result.results] == ["A", "B", "C"]


def test_resume_reruns_only_retryable_failures(tmp_path):
    out = tmp_path / "out.jsonl"
    script = {"A": failed, "B": lambda f: failed(f, "tls"), "C": blocked, "D": lambda f: failed(f, "budget")}
    run_triage(firms("A", "B", "C", "D", "E"), Stub(script), out)
    stub = Stub()
    messages = []
    result = run_triage(firms("A", "B", "C", "D", "E"), stub, out, resume=True, report=messages.append)
    assert stub.calls == ["A", "D"]                      # timeout and budget are retryable; tls, block, ok are kept
    assert result.resumed == 3
    assert any("retrying" in m for m in messages)
    assert {r.firm.name: r.status for r in result.results} == {
        "A": "ok", "B": "failed", "C": "blocked", "D": "ok", "E": "ok"}


def test_the_last_line_of_a_firm_wins(tmp_path):
    out = tmp_path / "out.jsonl"
    run_triage(firms("A"), Stub({"A": failed}), out)
    run_triage(firms("A"), Stub(), out, resume=True)             # A is retried: now two lines for A
    stored, _ = read_results(out)
    assert len(lines(out)) == 2
    assert stored[firm_key(firms("A")[0])].status == "ok"
    assert run_triage(firms("A"), Stub(), out, resume=True).resumed == 1


def test_a_half_written_last_line_is_ignored_and_the_next_line_starts_fresh(tmp_path):
    out = tmp_path / "out.jsonl"
    run_triage(firms("A"), Stub(), out)
    with out.open("a", encoding="utf-8") as handle:
        handle.write('{"firm": {"name": "B", "care')           # the process died mid-write: no newline
    stub = Stub()
    result = run_triage(firms("A", "B"), stub, out, resume=True)
    assert stub.calls == ["B"]
    assert any("line 2" in w and "ignored" in w for w in result.warnings)
    stored, warnings = read_results(out)
    assert set(stored) == {firm_key(f) for f in firms("A", "B")} and len(warnings) == 1


@pytest.mark.parametrize("junk", ["[]", "5", '"text"', "{}", "not json", '{"firm": 5}'])
def test_unreadable_lines_are_ignored_and_reported(tmp_path, junk):
    out = tmp_path / "out.jsonl"
    run_triage(firms("A"), Stub(), out)
    with out.open("a", encoding="utf-8") as handle:
        handle.write(junk + "\n")
    result = run_triage(firms("A"), Stub(), out, resume=True)
    assert result.resumed == 1 and len(result.warnings) == 1


def test_damaged_bytes_make_only_that_line_unreadable(tmp_path):
    out = tmp_path / "out.jsonl"
    run_triage(firms("A"), Stub(), out)
    with out.open("ab") as handle:
        handle.write(b"\xff\xfe broken \xff\n")
    result = run_triage(firms("A", "B"), Stub(), out, resume=True)
    assert result.resumed == 1 and len(result.warnings) == 1
    assert "B" in [r.firm.name for r in result.results]


def test_firms_with_the_same_name_but_another_careers_url_are_different(tmp_path):
    out = tmp_path / "out.jsonl"
    pair = [FirmInput("A", "https://one.example.test"), FirmInput("A", "https://two.example.test")]
    stub = Stub()
    result = run_triage(pair, stub, out)
    assert stub.calls == ["A", "A"] and len(result.results) == 2


def test_a_repeated_input_row_runs_once_and_is_reported(tmp_path):
    stub = Stub()
    result = run_triage(firms("A", "A", "B"), stub, tmp_path / "out.jsonl")
    assert stub.calls == ["A", "B"] and len(result.results) == 2
    assert any("more than once" in w for w in result.warnings)


def test_needs_rerun_is_exactly_failed_and_retryable():
    f = firms("A")[0]
    assert needs_rerun(failed(f, "timeout")) and needs_rerun(failed(f, "http_5xx"))
    assert not needs_rerun(failed(f, "tls")) and not needs_rerun(failed(f, "http_4xx"))
    assert not needs_rerun(blocked(f)) and not needs_rerun(ok(f))


# -- the summary ---------------------------------------------------------------------------------------------


def make_results(**counts):
    results = []
    for verdict, count in counts.items():
        for number in range(count):
            f = FirmInput(f"{verdict}-{number}", f"https://{verdict}{number}.example.test")
            results.append({
                "automatic": lambda f: ok(f, "feed"), "probable": ok, "manual": lambda f: TriageResult.ok(
                    f, "manual", [Evidence("note", "https://x.test", "n")]),
                "blocked": blocked, "unresolved": failed,
            }[verdict](f))
    return results


def test_the_summary_counts_every_verdict_and_names_the_gate():
    text = format_summary(make_results(automatic=2, probable=5, manual=1, blocked=1, unresolved=1))
    assert "10 firm(s)" in text
    assert "automatic 2, probable 5, manual 1, blocked 1, unresolved 1" in text
    assert "Gate (threshold 8): UNDECIDED" in text      # best case 2 + 5 + 1 = 8 is not below the threshold


def test_the_gate_passes_with_eight_automatic_firms():
    assert "PASS" in format_summary(make_results(automatic=8, probable=1))


def test_the_gate_fails_only_when_even_the_best_case_is_below_the_threshold():
    assert "Gate (threshold 8): FAIL" in format_summary(make_results(automatic=1, manual=5, blocked=3))
    assert "UNDECIDED" in format_summary(make_results(automatic=1, probable=4, unresolved=3))


def test_a_manual_or_blocked_firm_never_counts_towards_the_gate():
    text = format_summary(make_results(automatic=7, manual=5, blocked=5))
    assert "FAIL" in text                 # 7 automatic and nothing that could still become one


def test_the_threshold_can_be_changed():
    assert "Gate (threshold 2): PASS" in format_summary(make_results(automatic=2), threshold=2)


def test_blocked_and_unresolved_firms_are_listed_with_their_reason():
    text = format_summary(make_results(blocked=1, unresolved=1, automatic=1))
    assert "Blocked: blocked-0 (http_403)" in text
    assert "Unresolved: unresolved-0 (timeout)" in text


def test_the_summary_shows_how_many_results_were_resumed_and_the_terms_counts():
    text = format_summary(make_results(automatic=2), resumed=2)
    assert "2 taken from the output file" in text
    assert "terms of use (item 10 stays a human judgment): not_assessed 2" in text


def test_the_gate_counts_resumed_and_new_results_together(tmp_path):
    out = tmp_path / "out.jsonl"
    names = [f"F{n}" for n in range(8)]
    run_triage(firms(*names[:5]), Stub(default=lambda f: ok(f, "feed")), out)
    final = run_triage(firms(*names), Stub(default=lambda f: ok(f, "feed")), out, resume=True)
    assert final.resumed == 5 and len(final.results) == 8
    assert "Gate (threshold 8): PASS" in format_summary(final.results, resumed=final.resumed)
