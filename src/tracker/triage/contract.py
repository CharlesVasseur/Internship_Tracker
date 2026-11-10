"""The triage contract (step T1).

A triage run answers three separate questions for one firm:

    status  did the check work?                  ok / empty / failed / blocked
    mode    how could the firm be monitored?     feed, json-ld, json-api, vendor, ...
    verdict what do we do about it?              automatic / probable / manual / ...

The result is always built through this module, which refuses to build a
result that breaks the rules (for example a "failed" result without an error).
Inputs (FirmInput) are deliberately permissive: a bad URL must become a
"failed" result later, never an exception while reading the input.

Pure Python standard library. No network access in this file.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, get_args

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

Status = Literal["ok", "empty", "failed", "blocked"]
Mode = Literal[
    "feed", "json-ld", "json-api", "vendor", "html", "js-only", "manual", "blocked", "unknown"
]
Verdict = Literal["automatic", "probable", "manual", "blocked", "unresolved"]
GateDecision = Literal["PASS", "FAIL", "UNDECIDED"]
TermsStatus = Literal["flagged", "not_flagged", "not_assessed"]

STATUSES: tuple[str, ...] = get_args(Status)
MODES: tuple[str, ...] = get_args(Mode)
VERDICTS: tuple[str, ...] = get_args(Verdict)
TERMS_STATUSES: tuple[str, ...] = get_args(TermsStatus)

# Modes a successful ("ok") triage may report.
OK_MODES: tuple[str, ...] = (
    "feed", "json-ld", "json-api", "vendor", "html", "js-only", "manual",
)

# What was observed. Every classification must point to evidence of these kinds.
EVIDENCE_KINDS: frozenset[str] = frozenset({
    "robots",             # what robots.txt said (rules, Crawl-delay, Sitemap lines)
    "redirect_chain",     # the hosts a request was redirected through
    "feed_link",          # a <link rel=alternate> that looks like RSS/Atom
    "feed_parsed",        # a feed was fetched, parsed, and its items link to job-like URLs
    "jsonld_jobposting",  # a JobPosting JSON-LD block was seen
    "vendor_host",        # a hostname of a known recruiting system
    "job_links",          # several job-like links in the raw HTML
    "sitemap_line",       # a Sitemap: line in robots.txt (contents not checked)
    "sitemap_jobs",       # a sitemap was fetched and lists job-like URLs
    "spa_shell",          # empty app shell: no job content, JavaScript app markers
    "api_json",           # a JSON endpoint (given as a hint) was fetched and parsed
    "policy",             # decided by policy, e.g. LinkedIn/Indeed are never fetched
    "note",               # anything else worth keeping, quoted or counted
})

# The kind of evidence that must be present for each mode.
REQUIRED_EVIDENCE: dict[str, str] = {
    "feed": "feed_parsed",
    "json-ld": "jsonld_jobposting",
    "json-api": "api_json",
    "vendor": "vendor_host",
    "html": "job_links",
    "js-only": "spa_shell",
}

# A JSON-LD hit only counts as "automatic" if we also saw a way to find job URLs.
URL_SOURCE_KINDS: frozenset[str] = frozenset({"job_links", "sitemap_jobs"})

# Error kinds for status "failed", with whether trying again later makes sense.
ERROR_KINDS: dict[str, bool] = {
    "timeout": True,
    "dns": True,
    "connection": True,
    "http_5xx": True,
    "robots_unreachable": True,
    "budget": True,           # the per-firm time budget ran out
    "http_4xx": False,        # a normal page answered 4xx (not 401/403/429): wrong URL or moved page
    "tls": False,             # a certificate error does not fix itself on retry
    "decode": False,
    "too_large": False,
    "not_text": False,
    "invalid_url": False,
    "redirect_loop": False,
    "internal": False,        # a bug in our code: needs a fix, not a retry
}

# Reasons for status "blocked". We never work around any of them.
BLOCK_REASONS: frozenset[str] = frozenset({
    "robots_disallow", "http_401", "http_403", "http_429",
    "challenge_page", "captcha", "login_wall",
})

MAX_DETAIL = 300    # characters of one piece of evidence
MAX_MESSAGE = 500   # characters of an error message
MAX_QUOTE = 400     # characters of one quoted terms sentence
CUT_MARK = " [...cut]"


class ContractError(ValueError):
    """Raised when our own code tries to build a result that breaks the contract.

    This is a bug in the caller, not a problem with a firm. The orchestrator
    catches it and records the firm as failed/internal, so one bug never
    stops a run.
    """


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - len(CUT_MARK)] + CUT_MARK


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FirmInput:
    """One firm as it arrives from the CSV. Never validated: bad input is a result."""

    name: str
    careers_url: str
    listing_url: str | None = None      # optional hints (our survey firms have them)
    example_job_url: str | None = None
    api_url: str | None = None          # a JSON endpoint a human captured in the browser
    terms_url: str | None = None


# ---------------------------------------------------------------------------
# Output parts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """One observed fact: what, where, and a short quoted or counted detail."""

    kind: str
    url: str
    detail: str

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_KINDS:
            raise ContractError(f"unknown evidence kind: {self.kind!r}")
        if len(self.detail) > MAX_DETAIL:
            raise ContractError(f"evidence detail is {len(self.detail)} characters (max {MAX_DETAIL})")

    @classmethod
    def of(cls, kind: str, url: str, detail: str) -> Evidence:
        """Like the constructor, but cuts a long detail instead of refusing it."""
        return cls(kind, url, _cut(detail, MAX_DETAIL))


@dataclass(frozen=True)
class TriageError:
    """Why a check could not be done (status "failed")."""

    kind: str
    message: str
    url: str | None
    retryable: bool

    def __post_init__(self) -> None:
        if self.kind not in ERROR_KINDS:
            raise ContractError(f"unknown error kind: {self.kind!r}")
        if not self.message.strip():
            raise ContractError("an error needs a message")
        if len(self.message) > MAX_MESSAGE:
            raise ContractError(f"error message is {len(self.message)} characters (max {MAX_MESSAGE})")

    @classmethod
    def of(
        cls, kind: str, message: str, url: str | None = None, retryable: bool | None = None
    ) -> TriageError:
        """Build an error; "retryable" defaults from the kind, and long messages are cut."""
        if kind not in ERROR_KINDS:
            raise ContractError(f"unknown error kind: {kind!r}")
        flag = ERROR_KINDS[kind] if retryable is None else retryable
        return cls(kind, _cut(message.strip() or kind, MAX_MESSAGE), url, flag)


@dataclass(frozen=True)
class BlockInfo:
    """Why we were refused (status "blocked")."""

    reason: str
    url: str
    http_status: int | None = None

    def __post_init__(self) -> None:
        if self.reason not in BLOCK_REASONS:
            raise ContractError(f"unknown block reason: {self.reason!r}")


@dataclass(frozen=True)
class TermsFlag:
    """Item 10. The script flags and quotes; a human judges. It never says "no".

    flagged       sentences about automated access were found, quoted below
    not_flagged   the script found nothing. This is NOT a clearance.
    not_assessed  PDF, unsupported language, JavaScript page, or the fetch failed
    """

    url: str | None
    status: str
    quotes: tuple[str, ...] = ()
    language_guess: str = "unknown"
    why: str = ""

    def __post_init__(self) -> None:
        if self.status not in TERMS_STATUSES:
            raise ContractError(f"unknown terms status: {self.status!r}")
        if any(len(q) > MAX_QUOTE for q in self.quotes):
            raise ContractError(f"a quote is longer than {MAX_QUOTE} characters")
        if self.status == "flagged" and not 1 <= len(self.quotes) <= 2:
            raise ContractError("a flagged terms result needs 1 or 2 quotes")
        if self.status != "flagged" and self.quotes:
            raise ContractError("only a flagged terms result may carry quotes")
        if self.status == "not_assessed" and not self.why.strip():
            raise ContractError("a not_assessed terms result must say why")


# ---------------------------------------------------------------------------
# The result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TriageResult:
    firm: FirmInput
    status: str
    mode: str
    checked_at: str
    evidence: tuple[Evidence, ...] = ()
    error: TriageError | None = None
    block: BlockInfo | None = None
    terms: TermsFlag | None = None
    requests: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ContractError(f"unknown status: {self.status!r}")
        if self.mode not in MODES:
            raise ContractError(f"unknown mode: {self.mode!r}")
        try:
            dt.datetime.fromisoformat(self.checked_at)
        except (TypeError, ValueError):
            raise ContractError(f"checked_at is not an ISO timestamp: {self.checked_at!r}") from None
        if not isinstance(self.evidence, tuple) or not all(isinstance(e, Evidence) for e in self.evidence):
            raise ContractError("evidence must be a tuple of Evidence")
        if not isinstance(self.requests, tuple):
            raise ContractError("requests must be a tuple of URLs")

        if self.status == "failed":
            if self.error is None:
                raise ContractError("a failed result needs an error")
            if self.block is not None:
                raise ContractError("a failed result cannot carry a block")
            if self.mode != "unknown":
                raise ContractError("a failed result must have mode 'unknown'")
        elif self.status == "blocked":
            if self.block is None:
                raise ContractError("a blocked result needs block info")
            if self.error is not None:
                raise ContractError("a blocked result cannot carry an error")
            if self.mode != "blocked":
                raise ContractError("a blocked result must have mode 'blocked'")
        else:  # ok or empty
            if self.error is not None or self.block is not None:
                raise ContractError(f"a {self.status} result cannot carry an error or a block")
            if self.status == "empty":
                if self.mode != "unknown":
                    raise ContractError("an empty result must have mode 'unknown'")
            else:
                if self.mode not in OK_MODES:
                    raise ContractError(f"an ok result cannot have mode {self.mode!r}")
                if not self.evidence:
                    raise ContractError("an ok result needs at least one piece of evidence")
                needed = REQUIRED_EVIDENCE.get(self.mode)
                if needed and not any(e.kind == needed for e in self.evidence):
                    raise ContractError(f"mode {self.mode!r} needs evidence of kind {needed!r}")

    # -- derived ----------------------------------------------------------

    @property
    def verdict(self) -> str:
        return derive_verdict(self)

    # -- convenience constructors -----------------------------------------

    @classmethod
    def ok(
        cls, firm: FirmInput, mode: str, evidence: Iterable[Evidence], *,
        terms: TermsFlag | None = None, requests: Iterable[str] = (),
        checked_at: str | None = None,
    ) -> TriageResult:
        return cls(firm, "ok", mode, checked_at or utc_now_iso(), tuple(evidence),
                   None, None, terms, tuple(requests))

    @classmethod
    def empty(
        cls, firm: FirmInput, evidence: Iterable[Evidence] = (), *,
        terms: TermsFlag | None = None, requests: Iterable[str] = (),
        checked_at: str | None = None,
    ) -> TriageResult:
        return cls(firm, "empty", "unknown", checked_at or utc_now_iso(), tuple(evidence),
                   None, None, terms, tuple(requests))

    @classmethod
    def failed(
        cls, firm: FirmInput, error: TriageError, evidence: Iterable[Evidence] = (), *,
        terms: TermsFlag | None = None, requests: Iterable[str] = (),
        checked_at: str | None = None,
    ) -> TriageResult:
        return cls(firm, "failed", "unknown", checked_at or utc_now_iso(), tuple(evidence),
                   error, None, terms, tuple(requests))

    @classmethod
    def blocked(
        cls, firm: FirmInput, block: BlockInfo, evidence: Iterable[Evidence] = (), *,
        terms: TermsFlag | None = None, requests: Iterable[str] = (),
        checked_at: str | None = None,
    ) -> TriageResult:
        return cls(firm, "blocked", "blocked", checked_at or utc_now_iso(), tuple(evidence),
                   None, block, terms, tuple(requests))

    # -- serialisation (one JSON line per firm) ----------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "firm": _plain(self.firm),
            "status": self.status,
            "mode": self.mode,
            "verdict": self.verdict,
            "checked_at": self.checked_at,
            "evidence": [_plain(e) for e in self.evidence],
            "error": _plain(self.error) if self.error else None,
            "block": _plain(self.block) if self.block else None,
            "terms": _plain(self.terms) if self.terms else None,
            "requests": list(self.requests),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TriageResult:
        """Rebuild a result. The stored verdict is ignored and recomputed."""
        try:
            terms = data.get("terms")
            return cls(
                firm=FirmInput(**data["firm"]),
                status=data["status"],
                mode=data["mode"],
                checked_at=data["checked_at"],
                evidence=tuple(Evidence(**e) for e in data.get("evidence", [])),
                error=TriageError(**data["error"]) if data.get("error") else None,
                block=BlockInfo(**data["block"]) if data.get("block") else None,
                terms=TermsFlag(**{**terms, "quotes": tuple(terms.get("quotes", []))}) if terms else None,
                requests=tuple(data.get("requests", [])),
            )
        except (KeyError, TypeError) as exc:
            raise ContractError(f"cannot rebuild a result from this data: {exc}") from exc

    @classmethod
    def from_json(cls, line: str) -> TriageResult:
        try:
            return cls.from_dict(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ContractError(f"not valid JSON: {exc}") from exc


def _plain(obj: Any) -> dict[str, Any]:
    """A frozen dataclass as a plain dict (tuples become lists for JSON)."""
    out: dict[str, Any] = {}
    for name in obj.__dataclass_fields__:
        value = getattr(obj, name)
        out[name] = list(value) if isinstance(value, tuple) else value
    return out


# ---------------------------------------------------------------------------
# Verdict and gate
# ---------------------------------------------------------------------------


def derive_verdict(result: TriageResult) -> str:
    """What we do about a firm. A pure function of the result, covered by tests.

    automatic   can be monitored by the script
    probable    likely works, needs a check or an adapter
    manual      the user checks by hand
    blocked     refused; reported as blocked, never as "not monitorable"
    unresolved  failed or empty: try again or look by hand
    """
    if result.status == "blocked":
        return "blocked"
    if result.status in ("failed", "empty"):
        return "unresolved"
    mode = result.mode
    if mode == "feed":
        return "automatic"
    if mode == "json-ld":
        has_url_source = any(e.kind in URL_SOURCE_KINDS for e in result.evidence)
        return "automatic" if has_url_source else "probable"
    if mode in ("vendor", "html", "json-api"):
        return "probable"
    return "manual"  # js-only, manual


@dataclass(frozen=True)
class GateReport:
    decision: str            # PASS, FAIL or UNDECIDED
    threshold: int
    total: int
    counts: dict[str, int]   # verdict -> number of firms (all five verdicts present)
    explanation: str


def gate_decision(results: Iterable[TriageResult], threshold: int = 8) -> GateReport:
    """The go/no-go gate.

    PASS      at least `threshold` firms are automatic.
    FAIL      only if even automatic + probable + unresolved is below the threshold.
    UNDECIDED otherwise: the probable and unresolved firms must be resolved first.

    Blocked and manual firms never count towards the threshold. They are
    reported as blocked / manual, never as "not monitorable".
    """
    if threshold < 1:
        raise ContractError("the gate threshold must be at least 1")
    items = list(results)
    if not items:
        raise ContractError("the gate needs at least one result")
    counts = {verdict: 0 for verdict in VERDICTS}
    for item in items:
        counts[item.verdict] += 1
    automatic = counts["automatic"]
    best_case = automatic + counts["probable"] + counts["unresolved"]
    if automatic >= threshold:
        decision = "PASS"
    elif best_case < threshold:
        decision = "FAIL"
    else:
        decision = "UNDECIDED"
    explanation = (
        f"{automatic} automatic of {len(items)}; needs {threshold}. "
        f"Best case if every probable and unresolved firm works out: {best_case}. "
        f"Blocked ({counts['blocked']}) and manual ({counts['manual']}) do not count."
    )
    return GateReport(decision, threshold, len(items), counts, explanation)
