"""Load the synthetic scenarios in tests/fixtures/scenarios (test support, not a test file).

A scenario is one folder with a `scenario.json` and the page files it points to:

    description   one sentence: what the scenario shows
    firm          the FirmInput fields (name, careers_url, listing_url, ...)
    responses     for each URL, what the fake transport answers (see below)
    expected      what triage must conclude (see `mismatches`)

A response is one object, or a list of objects served in order (the last repeats):

    {"file": "listing.html"}                       a file of the folder; the content type comes from
                                                   its extension unless "content_type" is given
    {"file": "sitemap.xml", "truncated": true}     the transport reports a body cut at the size cap
    {"status": 404, "body": "Not found"}           an inline answer (status defaults to 200)
    {"error": "timeout", "message": "..."}         a transport error of that kind (TransportError)
    {"redirect": "https://..."}                    a redirect (status defaults to 301)
    optional in any object: "status", "content_type", "headers"

No network: everything is served by the FakeTransport of tests/fake_transport.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from fake_transport import FakeTransport
from tracker.triage.contract import FirmInput, TriageResult
from tracker.triage.transport import RawResponse, TransportError

SCENARIOS_DIR = Path(__file__).parent / "fixtures" / "scenarios"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".xml": "application/xml",
    ".txt": "text/plain; charset=utf-8",
    ".pdf": "application/pdf",
    ".json": "application/json",
}
RESPONSE_KEYS = {"file", "body", "error", "message", "redirect", "status", "content_type", "headers", "truncated"}
EXPECTED_KEYS = {
    "status", "mode", "verdict", "error_kind", "retryable", "block_reason", "terms_status",
    "evidence_kinds_include", "evidence_kinds_exclude", "notes_include",
    "requests_include", "requests_exclude", "no_requests",
}


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    folder: Path
    firm: FirmInput
    responses: dict
    expected: dict


def load_scenarios(root: Path = SCENARIOS_DIR) -> list[Scenario]:
    scenarios = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        document = json.loads((folder / "scenario.json").read_text(encoding="utf-8"))
        scenarios.append(Scenario(
            name=folder.name, description=document["description"], folder=folder,
            firm=FirmInput(**document["firm"]), responses=document["responses"], expected=document["expected"]))
    return scenarios


def build_transport(scenario: Scenario, clock=None) -> FakeTransport:
    transport = FakeTransport(clock)
    for url, spec in scenario.responses.items():
        specs = spec if isinstance(spec, list) else [spec]
        transport.add(url, *[_answer(scenario, item) for item in specs])
    return transport


def _answer(scenario: Scenario, spec: dict):
    unknown = set(spec) - RESPONSE_KEYS
    if unknown:
        raise ValueError(f"{scenario.name}: unknown response key(s) {sorted(unknown)}")
    if "error" in spec:
        return TransportError(spec["error"], spec.get("message", "scripted error"))
    headers = {name.lower(): value for name, value in spec.get("headers", {}).items()}
    if "redirect" in spec:
        headers["location"] = spec["redirect"]
        return RawResponse(spec.get("status", 301), headers, b"", False)
    if "file" in spec:
        path = scenario.folder / spec["file"]
        data = path.read_bytes()
        content_type = spec.get("content_type") or CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    else:
        data = spec.get("body", "").encode("utf-8")
        content_type = spec.get("content_type", "text/html; charset=utf-8")
    headers["content-type"] = content_type
    return RawResponse(spec.get("status", 200), headers, data, bool(spec.get("truncated", False)))


def mismatches(result: TriageResult, expected: dict) -> list[str]:
    """Why `result` does not match `expected`; an empty list means it matches.

    status, mode, verdict          must be equal
    error_kind, retryable          the result's error (must exist if asked)
    block_reason                   the result's block reason
    terms_status                   the terms flag status
    evidence_kinds_include/exclude evidence kinds that must / must not be present
    notes_include                  texts that must appear in the note evidence
    requests_include/exclude       URLs that must / must not appear in the requests
    no_requests                    true: nothing at all was requested
    """
    problems: list[str] = []

    def check(label, actual, wanted):
        if actual != wanted:
            problems.append(f"{label}: expected {wanted!r}, got {actual!r}")

    for key in ("status", "mode", "verdict"):
        check(key, getattr(result, key), expected[key])
    if "error_kind" in expected:
        check("error kind", result.error.kind if result.error else None, expected["error_kind"])
    if "retryable" in expected:
        check("retryable", result.error.retryable if result.error else None, expected["retryable"])
    if "block_reason" in expected:
        check("block reason", result.block.reason if result.block else None, expected["block_reason"])
    if "terms_status" in expected:
        check("terms status", result.terms.status if result.terms else None, expected["terms_status"])
    kinds = {item.kind for item in result.evidence}
    for kind in expected.get("evidence_kinds_include", []):
        if kind not in kinds:
            problems.append(f"evidence kind {kind!r} is missing (found {sorted(kinds)})")
    for kind in expected.get("evidence_kinds_exclude", []):
        if kind in kinds:
            problems.append(f"evidence kind {kind!r} should not be present")
    notes = " ".join(item.detail for item in result.evidence if item.kind == "note")
    for text in expected.get("notes_include", []):
        if text not in notes:
            problems.append(f"no note contains {text!r}")
    for url in expected.get("requests_include", []):
        if url not in result.requests:
            problems.append(f"{url} was not requested")
    for url in expected.get("requests_exclude", []):
        if url in result.requests:
            problems.append(f"{url} should not have been requested")
    if expected.get("no_requests") and result.requests:
        problems.append(f"no request expected, got {len(result.requests)}")
    return problems
