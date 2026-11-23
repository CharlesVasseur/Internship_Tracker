"""Offline tests for monitoring configuration and check execution."""

import json

import pytest

from fake_transport import FakeClock, FakeTransport, reply, robots_ok
from tracker.db import connect, init_db
import tracker.monitor as monitor_module
from tracker.monitor import load_monitoring_config, run_check
from tracker.service import (
    add_manual_opportunity,
    disable_firm_monitoring,
    set_firm_monitoring,
)
from tracker.triage.fetcher import Fetcher
from tracker.triage.transport import TransportError


LIST_URL = "https://jobs.example.test/list"
API_TEMPLATE = "https://jobs.example.test/api?page={page}"
TERMS_URL = "https://jobs.example.test/terms"


def card(card_id: str) -> str:
    return (
        f'<div class="job-card" id="{card_id}">'
        '<div class="job-card-overlay"><a href="https://jobs.example.test/job/'
        f'{card_id}"></a></div><div class="job-card-title">Example Intern {card_id}</div>'
        '<div class="icon city">Example City</div></div>'
    )


def page(card_ids: list[str]) -> str:
    return (
        f'<div class="cards-container"><div class="jobs-count">{len(card_ids)}</div>'
        + "".join(card(card_id) for card_id in card_ids)
        + "</div>"
    )


def api_page(card_ids: list[str], end: bool = True) -> str:
    return json.dumps(
        {
            "html": '<div class="cards-container">'
            + "".join(card(card_id) for card_id in card_ids)
            + "</div>",
            "jobsCount": len(card_ids),
            "nothingMoreToDisplay": end,
        }
    )


def make_fetcher(
    card_ids: list[str],
    *,
    status: int = 200,
    announced: int | None = None,
) -> Fetcher:
    clock = FakeClock()
    transport = FakeTransport(clock)
    transport.add("https://jobs.example.test/robots.txt", robots_ok())
    list_html = page(card_ids)
    if announced is not None:
        list_html = (
            '<div class="cards-container">'
            f'<div class="jobs-count">{announced}</div>'
            + "".join(card(card_id) for card_id in card_ids)
            + "</div>"
        )
    transport.add(LIST_URL, reply(status, body=list_html))
    if status == 200:
        transport.add(
            API_TEMPLATE.format(page=2),
            reply(body=api_page([], True), ctype="application/json"),
        )
    return Fetcher(
        transport,
        "InternshipTracker/0.1 (test; contact test@example.test)",
        sleep=clock.sleep,
        clock=clock,
    )


def seed(tmp_path):
    database_path = tmp_path / "tracker.db"
    init_db(database_path)
    connection = connect(database_path)
    connection.execute(
        "INSERT INTO firms (profile, name, country) VALUES (?, ?, ?)",
        ("profile", "Example Firm", "Example Country"),
    )
    firm_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
    return connection, firm_id


def configure(connection, firm_id):
    set_firm_monitoring(
        connection,
        firm_id,
        adapter="paged_cards",
        list_url=LIST_URL,
        api_url_template=API_TEMPLATE,
        terms_url=TERMS_URL,
        terms_reading="silent",
        terms_reviewed_on="2026-11-20",
        terms_note="Example note",
        on_date="2026-11-20",
    )


def test_set_and_disable_monitoring_preserves_configuration(tmp_path):
    connection, firm_id = seed(tmp_path)
    configure(connection, firm_id)
    row = connection.execute(
        "SELECT monitoring, careers_url, source_type, source_config "
        "FROM firms WHERE id = ?",
        (firm_id,),
    ).fetchone()
    config = json.loads(row["source_config"])
    assert row["monitoring"] == "automated"
    assert row["careers_url"] == LIST_URL
    assert row["source_type"] == "careers_page"
    assert config["first_api_page"] == 2
    assert config["terms"]["reviewed_on"] == "2026-11-20"

    disable_firm_monitoring(connection, firm_id)
    row = connection.execute(
        "SELECT monitoring, source_config FROM firms WHERE id = ?",
        (firm_id,),
    ).fetchone()
    assert row["monitoring"] == "manual"
    assert row["source_config"] is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("terms_reading", "forbids"),
        ("terms_reading", "unknown"),
        ("api_url_template", "https://jobs.example.test/api"),
        ("api_url_template", "https://jobs.example.test/api?page={page}&x={page}"),
        ("api_url_template", "https://jobs.example.test/api?page={page}&x={other}"),
        ("list_url", "not-a-url"),
        ("list_url", "https://other.example.test/list"),
        ("terms_reviewed_on", "2026-11-21"),
    ],
)
def test_monitoring_refusals_do_not_write(tmp_path, field, value):
    connection, firm_id = seed(tmp_path)
    before = dict(
        connection.execute(
            "SELECT monitoring, careers_url, source_type, source_config "
            "FROM firms WHERE id = ?",
            (firm_id,),
        ).fetchone()
    )
    values = {
        "adapter": "paged_cards",
        "list_url": LIST_URL,
        "api_url_template": API_TEMPLATE,
        "terms_url": TERMS_URL,
        "terms_reading": "silent",
        "terms_reviewed_on": "2026-11-20",
        "on_date": "2026-11-20",
    }
    values[field] = value
    with pytest.raises(ValueError):
        set_firm_monitoring(connection, firm_id, **values)
    after = dict(
        connection.execute(
            "SELECT monitoring, careers_url, source_type, source_config "
            "FROM firms WHERE id = ?",
            (firm_id,),
        ).fetchone()
    )
    assert after == before


def test_run_check_stores_results_and_respects_cooldown(tmp_path):
    connection, firm_id = seed(tmp_path)
    configure(connection, firm_id)

    first = run_check(
        connection,
        firm_id,
        fetcher=make_fetcher(["a", "b"]),
        now="2026-11-20T10:00:00",
    )
    assert first["status"] == "ok"
    assert first["new"] == 2
    assert first["requests"] == 2

    with pytest.raises(ValueError, match="wait 1 more minutes"):
        run_check(
            connection,
            firm_id,
            fetcher=make_fetcher(["a", "b"]),
            now="2026-11-20T10:29:00",
        )

    second = run_check(
        connection,
        firm_id,
        fetcher=make_fetcher(["a", "b"]),
        now="2026-11-20T10:31:00",
    )
    assert second["unchanged"] == 2

    third = run_check(
        connection,
        firm_id,
        fetcher=make_fetcher(["a"]),
        now="2026-11-20T11:02:00",
    )
    assert third["missing"] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM opportunities WHERE firm_id = ?", (firm_id,)
    ).fetchone()[0] == 2


def test_blocked_failed_and_empty_checks_do_not_change_opportunities(tmp_path):
    connection, firm_id = seed(tmp_path)
    configure(connection, firm_id)
    run_check(
        connection,
        firm_id,
        fetcher=make_fetcher(["a"]),
        now="2026-11-20T10:00:00",
    )
    manual_id = add_manual_opportunity(
        connection,
        firm_id,
        "Example Manual",
        url="https://jobs.example.test/manual",
        location="Example City",
    )
    before = connection.execute(
        "SELECT id, missing_count, state FROM opportunities WHERE firm_id = ? ORDER BY id",
        (firm_id,),
    ).fetchall()

    blocked = make_fetcher(["b"], status=429)
    result = run_check(
        connection,
        firm_id,
        fetcher=blocked,
        now="2026-11-20T10:31:00",
    )
    assert result["status"] == "blocked"
    after_block = connection.execute(
        "SELECT id, missing_count, state FROM opportunities WHERE firm_id = ? ORDER BY id",
        (firm_id,),
    ).fetchall()
    assert after_block == before

    failed = make_fetcher(["b"], announced=2)
    failed_transport = failed._transport
    failed_transport.add(
        API_TEMPLATE.format(page=2),
        TransportError("timeout", "Example timeout"),
    )
    result = run_check(
        connection,
        firm_id,
        fetcher=failed,
        now="2026-11-20T11:02:00",
    )
    assert result["status"] == "failed"
    after_failed = connection.execute(
        "SELECT id, missing_count, state FROM opportunities WHERE firm_id = ? ORDER BY id",
        (firm_id,),
    ).fetchall()
    assert after_failed == before
    assert connection.execute(
        "SELECT fingerprint FROM opportunities WHERE id = ?", (manual_id,)
    ).fetchone()["fingerprint"].startswith("manual:")


def test_empty_check_counts_stored_listings_as_missing(tmp_path):
    connection, firm_id = seed(tmp_path)
    configure(connection, firm_id)
    run_check(
        connection,
        firm_id,
        fetcher=make_fetcher(["a"]),
        now="2026-11-20T10:00:00",
    )

    empty_fetcher = make_fetcher([])
    result = run_check(
        connection,
        firm_id,
        fetcher=empty_fetcher,
        now="2026-11-20T10:31:00",
    )

    assert result["status"] == "empty"
    assert result["missing"] == 1


def test_adapter_exception_is_recorded_as_internal_failure(tmp_path, monkeypatch):
    connection, firm_id = seed(tmp_path)
    configure(connection, firm_id)

    def broken_adapter(fetcher, config):
        raise RuntimeError("Example adapter failure")

    monkeypatch.setitem(monitor_module.ADAPTERS, "paged_cards", broken_adapter)
    result = run_check(
        connection,
        firm_id,
        fetcher=make_fetcher([]),
        now="2026-11-20T10:00:00",
    )

    assert result["status"] == "failed"
    row = connection.execute(
        "SELECT error FROM checks WHERE firm_id = ?", (firm_id,)
    ).fetchone()
    assert row["error"] == "internal error: RuntimeError: Example adapter failure"


def test_run_check_refuses_without_setup_or_contact(tmp_path, monkeypatch):
    connection, firm_id = seed(tmp_path)
    with pytest.raises(ValueError, match="not enabled"):
        run_check(connection, firm_id, now="2026-11-20T10:00:00")

    configure(connection, firm_id)
    monkeypatch.delenv("TRACKER_CONTACT", raising=False)
    with pytest.raises(ValueError, match="contact address"):
        run_check(connection, firm_id, now="2026-11-20T10:00:00")
    assert connection.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == 0


def valid_monitoring_config():
    return {
        "adapter": "paged_cards",
        "list_url": LIST_URL,
        "api_url_template": API_TEMPLATE,
        "first_api_page": 2,
        "max_pages": 30,
        "terms": {
            "url": TERMS_URL,
            "reading": "silent",
            "reviewed_on": "2026-11-20",
            "note": "Example note",
        },
    }


def test_load_monitoring_config_returns_valid_config():
    config = valid_monitoring_config()

    assert load_monitoring_config(json.dumps(config)) == config


@pytest.mark.parametrize("source_config_text", [None, "", "   "])
def test_load_monitoring_config_refuses_none_or_empty(source_config_text):
    with pytest.raises(
        ValueError,
        match="^automatic checks are not enabled for this firm$",
    ):
        load_monitoring_config(source_config_text)


def test_load_monitoring_config_refuses_invalid_json():
    with pytest.raises(ValueError, match="valid JSON"):
        load_monitoring_config("{not-json")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"adapter": None}, "adapter is missing"),
        ({"adapter": "unknown_adapter"}, "unknown adapter"),
        ({"list_url": None}, "list_url is missing"),
        ({"api_url_template": None}, "api_url_template is missing"),
        ({"terms": None}, "terms of use review is missing"),
    ],
)
def test_load_monitoring_config_refuses_missing_or_unknown_fields(change, message):
    config = valid_monitoring_config()
    config.update(change)

    with pytest.raises(ValueError, match=message):
        load_monitoring_config(json.dumps(config))


@pytest.mark.parametrize("reading", ["unknown", "forbids"])
def test_load_monitoring_config_refuses_unknown_reading(reading):
    config = valid_monitoring_config()
    config["terms"]["reading"] = reading

    with pytest.raises(ValueError, match="invalid terms reading"):
        load_monitoring_config(json.dumps(config))


@pytest.mark.parametrize("reviewed_on", ["not-a-date", "2026-02-30", "20261120"])
def test_load_monitoring_config_refuses_invalid_reviewed_on(reviewed_on):
    config = valid_monitoring_config()
    config["terms"]["reviewed_on"] = reviewed_on

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        load_monitoring_config(json.dumps(config))


def test_load_monitoring_config_refuses_missing_terms_url():
    config = valid_monitoring_config()
    config["terms"].pop("url")

    with pytest.raises(ValueError, match="terms of use review url is missing"):
        load_monitoring_config(json.dumps(config))


def test_run_check_refuses_firm_with_bad_config(tmp_path):
    connection, firm_id = seed(tmp_path)
    connection.execute(
        "UPDATE firms SET monitoring = 'automated', source_config = ? WHERE id = ?",
        (json.dumps({"adapter": "paged_cards"}), firm_id),
    )

    with pytest.raises(ValueError, match="automatic checks are not enabled"):
        run_check(connection, firm_id, now="2026-11-20T10:00:00")


def test_run_check_builds_live_fetcher_offline_and_hides_contact(tmp_path, monkeypatch):
    connection, firm_id = seed(tmp_path)
    configure(connection, firm_id)
    clock = FakeClock()
    calls = []
    sent_headers = []

    def scripted_transport(url, headers, timeout):
        calls.append(url)
        sent_headers.append(dict(headers))
        if url == "https://jobs.example.test/robots.txt":
            return robots_ok()
        if url == LIST_URL:
            list_html = (
                '<div class="cards-container"><div class="jobs-count">4</div>'
                + card("a")
                + card("b")
                + "</div>"
            )
            return reply(body=list_html, ctype="text/html; charset=utf-8")
        if url == API_TEMPLATE.format(page=2):
            return reply(
                body=api_page(["c", "d"]),
                ctype="application/json; charset=utf-8",
            )
        raise AssertionError(f"unexpected request to {url}")

    monkeypatch.setattr(monitor_module, "urllib_transport", scripted_transport)
    monkeypatch.setattr(monitor_module.time, "sleep", clock.sleep)
    monkeypatch.setattr(monitor_module.time, "monotonic", clock)
    monkeypatch.delenv("TRACKER_CONTACT", raising=False)

    result = run_check(
        connection,
        firm_id,
        contact="test@example.test",
        now="2026-11-20T10:00:00",
    )

    assert result["status"] == "ok"
    assert calls[0] == "https://jobs.example.test/robots.txt"
    assert calls == [
        "https://jobs.example.test/robots.txt",
        LIST_URL,
        API_TEMPLATE.format(page=2),
    ]
    for headers in sent_headers:
        user_agent = headers["User-Agent"]
        assert "InternshipTracker" in user_agent
        assert "test@example.test" in user_agent

    check_row = connection.execute(
        "SELECT result FROM checks WHERE firm_id = ?", (firm_id,)
    ).fetchone()
    assert check_row["result"] == "ok"

    contact = "test@example.test"
    for table in ("firms", "checks", "opportunities", "applications", "activities"):
        columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
        for column in columns:
            if column["type"].upper() == "TEXT":
                values = connection.execute(
                    f'SELECT "{column["name"]}" FROM {table}'
                ).fetchall()
                for row in values:
                    assert contact not in (row[column["name"]] or "")


def test_run_check_rejects_invalid_contact_before_transport(tmp_path, monkeypatch):
    connection, firm_id = seed(tmp_path)
    configure(connection, firm_id)
    called = []

    def scripted_transport(url, headers, timeout):
        called.append(url)
        raise AssertionError("transport must not be called")

    monkeypatch.setattr(monitor_module, "urllib_transport", scripted_transport)
    monkeypatch.delenv("TRACKER_CONTACT", raising=False)

    with pytest.raises(ValueError, match="contact address"):
        run_check(
            connection,
            firm_id,
            contact="not-a-contact",
            now="2026-11-20T10:00:00",
        )

    assert called == []
