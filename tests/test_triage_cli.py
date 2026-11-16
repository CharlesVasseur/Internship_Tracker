"""Tests for the command-line tool (T4.4). Fake transports only; no network, no waiting."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fake_transport import ALLOW_ALL, FakeClock, FakeTransport, reply, robots_ok
from tracker.triage.cli import main
from tracker.triage.transport import TransportError

ROOT = Path(__file__).resolve().parent.parent
HEADER = "firm;careers_entry_url;listing_url;example_job_url;terms_url"
A = "https://a.example.test"
B = "https://b.example.test"
CONTACT = "tester@example.test"


def jobs_page():
    links = "".join(f'<a href="/jobs/{n}">Job {n}</a>' for n in range(1, 7))
    return f"<html><body><p>Open roles</p>{links}</body></html>"


def csv_file(tmp_path, *rows, name="firms.csv"):
    path = tmp_path / name
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def site(transport, base=A):
    transport.add(base + "/robots.txt", robots_ok(ALLOW_ALL)).add(base + "/careers", reply(200, jobs_page()))
    transport.add(base + "/jobs/1", reply(200, "<html><body>Intern</body></html>"))
    return transport


def row(name="Alpha", base=A):
    return f"{name};{base}/careers;;{base}/jobs/1;"


def run(tmp_path, *extra, rows=None, transport=None, environ=None, output="out.jsonl", cache="cache", sleep=None):
    firms = csv_file(tmp_path, *(rows if rows is not None else (row(),)))
    argv = ["--input", str(firms), "--output", str(tmp_path / output), "--cache-dir", str(tmp_path / cache), *extra]
    clock = FakeClock()                                   # time moves only when the tool "sleeps": tests are instant
    return main(argv, environ={"SURVEY_CONTACT": CONTACT} if environ is None else environ,
                live_transport=transport if transport is not None else FakeTransport(),
                sleep=sleep or clock.sleep, clock=clock)


def out_lines(tmp_path, output="out.jsonl"):
    path = tmp_path / output
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line]


# -- a normal live run --------------------------------------------------------------------------------


def test_a_live_run_writes_results_the_cache_and_a_summary(tmp_path, capsys):
    code = run(tmp_path, transport=site(FakeTransport()))
    shown = capsys.readouterr()
    assert code == 0
    assert [line["mode"] for line in out_lines(tmp_path)] == ["html"]
    assert list((tmp_path / "cache").rglob("*.json"))                      # every answer was saved
    assert "LIVE run" in shown.out and "[1/1] Alpha: ok html probable" in shown.out
    assert "Triage summary: 1 firm(s)" in shown.out and "Gate (threshold 8)" in shown.out
    assert shown.err == ""


def test_the_gate_result_is_not_an_error_exit(tmp_path, capsys):
    code = run(tmp_path, transport=site(FakeTransport()))
    assert code == 0 and "FAIL" in capsys.readouterr().out                # 1 probable firm cannot reach 8


def test_the_threshold_option_changes_the_gate(tmp_path, capsys):
    run(tmp_path, "--threshold", "1", transport=site(FakeTransport()))
    assert "Gate (threshold 1): UNDECIDED" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["0", "-3", "abc", "2.5"])
def test_a_bad_threshold_is_a_usage_error(tmp_path, value, capsys):
    assert run(tmp_path, "--threshold", value) == 2
    assert not (tmp_path / "out.jsonl").exists()


# -- the contact address -------------------------------------------------------------------------------


def test_a_live_run_without_a_contact_does_not_start(tmp_path, capsys):
    transport = FakeTransport()
    assert run(tmp_path, transport=transport, environ={}) == 2
    assert "SURVEY_CONTACT" in capsys.readouterr().err
    assert transport.calls == [] and not (tmp_path / "out.jsonl").exists()


def test_the_contact_comes_from_the_environment(tmp_path):
    transport = site(FakeTransport())
    run(tmp_path, transport=transport)
    assert CONTACT in transport.sent_headers[0]["User-Agent"]


def test_the_contact_option_beats_the_environment(tmp_path):
    transport = site(FakeTransport())
    run(tmp_path, "--contact", "other@example.test", transport=transport)
    assert "other@example.test" in transport.sent_headers[0]["User-Agent"]


def test_the_contact_is_not_stored_in_the_results_or_the_cache(tmp_path):
    run(tmp_path, transport=site(FakeTransport()))
    for path in tmp_path.rglob("*"):
        if path.is_file() and path.name != "firms.csv":
            assert CONTACT.encode() not in path.read_bytes(), path


# -- the input file -------------------------------------------------------------------------------------


@pytest.mark.parametrize("content, message", [
    (None, "cannot read the firm list"),
    ("firm,careers_entry_url\nA,https://a.test\n", "comma-separated"),
    ("name;url\nA;https://a.test\n", "required column"),
    ("", "no header"),
])
def test_an_unusable_firm_list_stops_the_run_before_any_request(tmp_path, capsys, content, message):
    path = tmp_path / "bad.csv"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    transport = FakeTransport()
    code = main(["--input", str(path), "--output", str(tmp_path / "o.jsonl")],
                environ={"SURVEY_CONTACT": CONTACT}, live_transport=transport)
    assert code == 2 and message in capsys.readouterr().err
    assert transport.calls == [] and not (tmp_path / "o.jsonl").exists()


def test_csv_warnings_are_shown_and_the_run_continues(tmp_path, capsys):
    code = run(tmp_path, rows=(f"Soci?t? Alpha;{A}/careers;;{A}/jobs/1;",), transport=site(FakeTransport()))
    assert code == 0 and "warning: line 2" in capsys.readouterr().err


def test_a_row_with_a_bad_address_becomes_a_result_not_a_crash(tmp_path, capsys):
    code = run(tmp_path, rows=("Alpha;not a url;;;",))
    assert code == 0
    line = out_lines(tmp_path)[0]
    assert (line["status"], line["error"]["kind"]) == ("failed", "invalid_url")


def test_linkedin_is_never_requested(tmp_path):
    transport = FakeTransport()                                              # nothing scripted: any request fails the test
    code = run(tmp_path, rows=("Alpha;https://www.linkedin.com/company/a/jobs;;;",), transport=transport)
    assert code == 0 and transport.calls == []
    assert out_lines(tmp_path)[0]["mode"] == "manual"


# -- the output file and resume ----------------------------------------------------------------------------


def test_an_existing_output_stops_the_run_without_touching_it(tmp_path, capsys):
    run(tmp_path, transport=site(FakeTransport()))
    before = (tmp_path / "out.jsonl").read_bytes()
    transport = FakeTransport()
    capsys.readouterr()
    assert run(tmp_path, transport=transport) == 2
    assert "--resume" in capsys.readouterr().err
    assert (tmp_path / "out.jsonl").read_bytes() == before and transport.calls == []


def test_resume_skips_finished_firms_without_any_request(tmp_path, capsys):
    run(tmp_path, transport=site(FakeTransport()))
    capsys.readouterr()
    transport = FakeTransport()
    assert run(tmp_path, "--resume", transport=transport) == 0
    shown = capsys.readouterr().out
    assert transport.calls == [] and "already done" in shown and "1 taken from the output file" in shown
    assert len(out_lines(tmp_path)) == 1


def test_a_write_problem_is_reported_with_exit_2(tmp_path, capsys):
    (tmp_path / "blocker").write_text("x")
    code = run(tmp_path, transport=site(FakeTransport()), output="blocker/out.jsonl")
    assert code == 2 and "cannot write the results" in capsys.readouterr().err


def test_ctrl_c_saves_finished_firms_and_exits_130_and_resume_finishes(tmp_path, capsys):
    rows = (row(), row("Beta", B))
    first = site(FakeTransport())
    first.add(B + "/robots.txt", KeyboardInterrupt())
    assert run(tmp_path, rows=rows, transport=first) == 130
    assert "--resume" in capsys.readouterr().err
    assert [line["firm"]["name"] for line in out_lines(tmp_path)] == ["Alpha"]
    second = site(FakeTransport(), B)
    assert run(tmp_path, "--resume", rows=rows, transport=second) == 0
    assert [line["firm"]["name"] for line in out_lines(tmp_path)] == ["Alpha", "Beta"]
    assert A + "/careers" not in second.calls


# -- replay -----------------------------------------------------------------------------------------------------


def test_replay_gives_the_same_results_without_network_contact_or_waiting(tmp_path, capsys):
    run(tmp_path, transport=site(FakeTransport()))
    capsys.readouterr()

    def no_sleep(seconds):
        raise AssertionError("replay must never wait")

    code = run(tmp_path, "--replay", environ={}, output="replayed.jsonl", transport=FakeTransport(), sleep=no_sleep)
    shown = capsys.readouterr().out
    assert code == 0 and "replay:" in shown and "LIVE" not in shown
    live, replayed = out_lines(tmp_path)[0], out_lines(tmp_path, "replayed.jsonl")[0]
    assert (live["mode"], live["requests"]) == (replayed["mode"], replayed["requests"])


def test_replay_never_calls_the_live_transport(tmp_path):
    run(tmp_path, transport=site(FakeTransport()))
    spy = FakeTransport()
    run(tmp_path, "--replay", output="again.jsonl", transport=spy)
    assert spy.calls == []


def test_replay_without_a_cache_folder_does_not_start(tmp_path, capsys):
    assert run(tmp_path, "--replay", cache="missing") == 2
    assert "cache folder" in capsys.readouterr().err
    assert not (tmp_path / "out.jsonl").exists()


def test_replay_shows_a_firm_missing_from_the_cache_as_a_failure(tmp_path):
    run(tmp_path, transport=site(FakeTransport()))
    rows = (row(), row("Beta", B))
    assert run(tmp_path, "--replay", rows=rows, output="replayed.jsonl") == 0
    lines = out_lines(tmp_path, "replayed.jsonl")
    assert lines[0]["status"] == "ok"
    assert (lines[1]["status"], lines[1]["error"]["kind"]) == ("failed", "internal")
    assert "not in the cache" in lines[1]["error"]["message"]


# -- usage and the module entry point -----------------------------------------------------------------------------


def test_help_exits_zero_and_names_the_options(capsys):
    assert main(["--help"]) == 0
    text = capsys.readouterr().out
    for option in ("--input", "--output", "--contact", "--cache-dir", "--replay", "--resume", "--threshold"):
        assert option in text


def test_the_input_option_is_required(capsys):
    assert main([]) == 2


def test_python_dash_m_runs_the_tool():
    done = subprocess.run(
        [sys.executable, "-m", "tracker.triage", "--help"], cwd=ROOT, capture_output=True, text=True,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""}, timeout=60)
    assert done.returncode == 0 and "--resume" in done.stdout
