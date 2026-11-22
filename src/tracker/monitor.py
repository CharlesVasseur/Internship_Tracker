"""Run configured automatic source checks without hiding failures."""

from datetime import date, datetime
import json
import os
import sqlite3

from tracker.adapters import ADAPTERS
from tracker.adapters.base import AdapterResult
from tracker.checks import apply_check_result
from tracker.service import MANUAL_CHECK_EVIDENCE, TERMS_READINGS
from tracker.triage.fetcher import Fetcher
from tracker.triage.transport import build_user_agent, urllib_transport


MIN_MINUTES_BETWEEN_CHECKS = 30


def load_monitoring_config(source_config_text) -> dict:
    """Parse and validate a firm's saved automatic-check configuration."""
    if source_config_text is None or (
        isinstance(source_config_text, str) and not source_config_text.strip()
    ):
        raise ValueError("automatic checks are not enabled for this firm")
    try:
        config = json.loads(source_config_text)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("source_config must be valid JSON") from error
    if not isinstance(config, dict):
        raise ValueError("source_config must be a JSON object")

    adapter_name = config.get("adapter")
    if not isinstance(adapter_name, str) or not adapter_name.strip():
        raise ValueError("source_config adapter is missing")
    if adapter_name not in ADAPTERS:
        raise ValueError(f"unknown adapter: {adapter_name}")

    list_url = config.get("list_url")
    if not isinstance(list_url, str) or not list_url.strip():
        raise ValueError("source_config list_url is missing")

    api_template = config.get("api_url_template")
    if not isinstance(api_template, str) or not api_template.strip():
        raise ValueError("source_config api_url_template is missing")
    if "{page}" not in api_template:
        raise ValueError("source_config api_url_template must contain {page}")

    terms = config.get("terms")
    if not isinstance(terms, dict):
        raise ValueError("terms of use review is missing")

    terms_url = terms.get("url")
    if not isinstance(terms_url, str) or not terms_url.strip():
        raise ValueError("terms of use review url is missing")

    reading = terms.get("reading")
    if reading not in TERMS_READINGS:
        raise ValueError(f"invalid terms reading: {reading}")

    reviewed_on = terms.get("reviewed_on")
    if not isinstance(reviewed_on, str):
        raise ValueError("terms reviewed_on must be YYYY-MM-DD")
    try:
        parsed_reviewed_on = date.fromisoformat(reviewed_on)
    except ValueError as error:
        raise ValueError("terms reviewed_on must be YYYY-MM-DD") from error
    if parsed_reviewed_on.isoformat() != reviewed_on:
        raise ValueError("terms reviewed_on must be YYYY-MM-DD")

    return config


def _check_cooldown(
    conn: sqlite3.Connection,
    firm_id: int,
    now: datetime,
) -> None:
    rows = conn.execute(
        """
        SELECT finished_at, evidence_snippet
        FROM checks
        WHERE firm_id = ?
        ORDER BY id DESC
        """,
        (firm_id,),
    ).fetchall()
    for row in rows:
        if (row["evidence_snippet"] or "").startswith(MANUAL_CHECK_EVIDENCE):
            continue
        try:
            finished = datetime.fromisoformat(row["finished_at"])
        except (TypeError, ValueError):
            continue
        elapsed = (now - finished).total_seconds()
        if 0 <= elapsed < MIN_MINUTES_BETWEEN_CHECKS * 60:
            minutes = int(elapsed // 60)
            remaining = MIN_MINUTES_BETWEEN_CHECKS - minutes
            raise ValueError(
                f"last automatic check was {minutes} minutes ago; "
                f"wait {remaining} more minutes"
            )
        return


def _contact_available(contact: str | None) -> bool:
    value = (contact or os.environ.get("TRACKER_CONTACT", "")).strip()
    return "@" in value or value.lower().startswith(("http://", "https://"))


def run_check(
    conn: sqlite3.Connection,
    firm_id: int,
    *,
    contact: str | None = None,
    fetcher: Fetcher | None = None,
    now: str | None = None,
) -> dict:
    """Run one automatic check and persist its typed outcome."""
    firm = conn.execute(
        "SELECT id, monitoring, source_config FROM firms WHERE id = ?",
        (firm_id,),
    ).fetchone()
    if firm is None:
        raise LookupError(f"unknown firm id: {firm_id}")

    if firm["monitoring"] != "automated":
        raise ValueError("automatic checks are not enabled for this firm")
    try:
        config = load_monitoring_config(firm["source_config"])
    except ValueError as error:
        if str(error).startswith("unknown adapter:"):
            raise
        raise ValueError("automatic checks are not enabled for this firm") from error
    adapter_name = config["adapter"]
    if adapter_name not in ADAPTERS:
        raise ValueError(f"unknown adapter: {adapter_name}")

    started_at = now or datetime.now().replace(microsecond=0).isoformat()
    started = datetime.fromisoformat(started_at)
    _check_cooldown(conn, firm_id, started)

    if fetcher is None and not _contact_available(contact):
        raise ValueError("a contact address is required: set TRACKER_CONTACT")

    if fetcher is None:
        effective_contact = contact or os.environ.get("TRACKER_CONTACT")
        user_agent = build_user_agent(effective_contact)
        fetcher = Fetcher(urllib_transport, user_agent)

    try:
        result = ADAPTERS[adapter_name](fetcher, config)
    except Exception as error:
        result = AdapterResult(
            "failed",
            error=f"internal error: {type(error).__name__}: {error}",
        )

    finished_at = (
        started_at
        if now is not None
        else datetime.now().replace(microsecond=0).isoformat()
    )
    summary = apply_check_result(
        conn,
        firm_id,
        result,
        started_at=started_at,
        finished_at=finished_at,
    )
    request_log = getattr(fetcher, "request_log", None)
    summary["requests"] = len(request_log) if request_log is not None else None
    return summary
