"""What one fetch gives back (step T2.4).

A fetch ends in exactly one of three ways, with the same words as the contract:

    ok       we got an answer and could read it as text        -> `text` is set
    blocked  the site or its robots.txt refused us             -> `block` is set
    failed   a technical problem stopped the check             -> `error` is set

Nothing here says "no openings". A failed or blocked fetch is a problem to
show, never an answer about the firm. The invariants are checked on creation,
like the contract's own.

Pure Python standard library.
"""

from __future__ import annotations

from dataclasses import dataclass

from tracker.triage.contract import BlockInfo, ContractError, TriageError

FETCH_STATUSES = ("ok", "blocked", "failed")


@dataclass(frozen=True)
class FetchResult:
    url: str                                  # the URL we were asked for
    final_url: str                            # where the redirects ended (same as url if none)
    status: str                               # ok / blocked / failed
    http_status: int | None = None            # the last HTTP status seen, if any
    content_type: str = ""
    text: str | None = None                   # only when status == "ok"
    charset_used: str | None = None
    charset_source: str | None = None         # header / bom / meta / default-utf8 / fallback-cp1252
    charset_note: str | None = None           # what was skipped or approximated
    redirect_chain: tuple[str, ...] = ()      # the URLs we were sent on to, in order
    error: TriageError | None = None          # only when status == "failed"
    block: BlockInfo | None = None            # only when status == "blocked"
    requests: tuple[str, ...] = ()            # every URL actually requested during this fetch
    truncated: bool = False                   # ok only: the text is just the start of a body over the size cap

    def __post_init__(self) -> None:
        if self.status not in FETCH_STATUSES:
            raise ContractError(f"unknown fetch status: {self.status!r}")
        if not isinstance(self.redirect_chain, tuple) or not isinstance(self.requests, tuple):
            raise ContractError("redirect_chain and requests must be tuples")
        has_text = self.text is not None
        if self.truncated and self.status != "ok":
            raise ContractError("only an ok fetch can be truncated")
        if self.status == "ok" and (not has_text or self.error or self.block):
            raise ContractError("an ok fetch has text and no error or block")
        if self.status == "failed" and (self.error is None or has_text or self.block):
            raise ContractError("a failed fetch has an error and no text or block")
        if self.status == "blocked" and (self.block is None or has_text or self.error):
            raise ContractError("a blocked fetch has a block and no text or error")

    @property
    def ok(self) -> bool:
        return self.status == "ok"
