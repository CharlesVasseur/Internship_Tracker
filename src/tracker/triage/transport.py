"""One HTTP GET, and nothing else (step T2.3).

A "transport" is any function  transport(url, headers, timeout) -> RawResponse
that either returns what the server answered or raises TransportError. The
fetcher (T2.4) owns everything clever: robots.txt, waiting, retries, redirects,
blocks. This file only makes a single request and describes what happened.

Rules kept here:
    * redirects are NOT followed (the fetcher follows them, one robots check per host)
    * HTTP 3xx, 4xx and 5xx are returned as answers, not raised
    * TLS certificate verification is never turned off
    * the body is read up to the size cap and flagged if it was cut
    * low-level errors become four typed kinds: timeout, dns, connection, tls
      (plus invalid_url); anything else is a bug in our code and is NOT hidden

Pure Python standard library. The live function below is the only code in the
project that opens a connection, and no test calls it (tests use fakes).
"""

from __future__ import annotations

import http.client
import os
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping

from tracker.triage.decode import MAX_BODY_BYTES

TIMEOUT_SECONDS = 15
MAX_WIRE_BYTES = MAX_BODY_BYTES    # the size cap, applied to the bytes as they arrive
TRANSPORT_KINDS = ("timeout", "dns", "connection", "tls", "invalid_url")
PROJECT_AGENT = "InternshipTracker/0.1"


@dataclass(frozen=True)
class RawResponse:
    """What the server answered. Body is as sent on the wire (maybe still gzip)."""

    status: int
    headers: dict[str, str]       # header names in lowercase
    body: bytes
    truncated: bool = False       # True if the body was longer than the cap and was cut


class TransportError(Exception):
    """The request did not get an answer. `kind` is one of TRANSPORT_KINDS."""

    def __init__(self, kind: str, message: str) -> None:
        if kind not in TRANSPORT_KINDS:
            raise ValueError(f"unknown transport error kind: {kind!r}")
        super().__init__(message)
        self.kind = kind
        self.message = message


Transport = Callable[[str, Mapping[str, str], float], RawResponse]


# ---------------------------------------------------------------------------
# User-Agent: names the project, carries the contact address given at run time
# ---------------------------------------------------------------------------


class ContactRequired(ValueError):
    """A live run needs a contact address. The CLI (T4) turns this into a clear message."""


def build_user_agent(contact: str | None, environ: Mapping[str, str] | None = None) -> str:
    """'InternshipTracker/0.1 (triage; contact you@example.com)'.

    The address comes from --contact, else from the SURVEY_CONTACT environment
    variable. It is never stored in the source code.
    """
    env = os.environ if environ is None else environ
    value = (contact or env.get("SURVEY_CONTACT", "")).strip()
    if "@" not in value and not value.lower().startswith(("http://", "https://")):
        raise ContactRequired(
            "A live run needs a contact address so site owners can reach you: "
            "pass --contact you@example.com (or set SURVEY_CONTACT)."
        )
    return f"{PROJECT_AGENT} (triage; contact {value})"


def request_headers(user_agent: str) -> dict[str, str]:
    """The only headers we send."""
    return {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}


# ---------------------------------------------------------------------------
# Turning low-level exceptions into typed errors
# ---------------------------------------------------------------------------


def classify_exception(exc: BaseException) -> TransportError | None:
    """A typed TransportError for network problems; None for anything else (a bug).

    The order matters: ssl, DNS and timeout errors are all kinds of OSError, so
    they are tested before the general "connection" case.
    """
    if isinstance(exc, TransportError):
        return exc
    detail = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, BaseException):
            return classify_exception(reason)
        text = str(reason)
        if "unknown url type" in text.lower():
            return TransportError("invalid_url", text)
        if "timed out" in text.lower():
            return TransportError("timeout", text)
        return TransportError("connection", text or detail)
    if isinstance(exc, ssl.SSLError):
        return TransportError("tls", detail)
    if isinstance(exc, socket.gaierror):
        return TransportError("dns", detail)
    if isinstance(exc, TimeoutError):
        return TransportError("timeout", detail)
    if isinstance(exc, http.client.InvalidURL):
        return TransportError("invalid_url", detail)
    if isinstance(exc, ValueError):       # includes UnicodeError from a bad host name
        return TransportError("invalid_url", detail)
    if isinstance(exc, (http.client.HTTPException, OSError)):
        return TransportError("connection", detail)
    return None


# ---------------------------------------------------------------------------
# The live request
# ---------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects: the 3xx answer is handed back to the fetcher."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect())


def read_capped(stream, limit: int = MAX_WIRE_BYTES) -> tuple[bytes, bool]:
    """Read at most `limit` bytes. The flag says whether more was waiting."""
    chunks: list[bytes] = []
    total = 0
    while True:
        piece = stream.read(64 * 1024)
        if not piece:
            return b"".join(chunks), False
        if total + len(piece) > limit:
            chunks.append(piece[: limit - total])
            return b"".join(chunks), True
        chunks.append(piece)
        total += len(piece)


def _lowercase(headers) -> dict[str, str]:
    return {name.lower(): value for name, value in headers.items()}


def _get(url: str, headers: Mapping[str, str], timeout: float) -> RawResponse:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with _build_opener().open(request, timeout=timeout) as response:
            body, truncated = read_capped(response, MAX_WIRE_BYTES)
            status = getattr(response, "status", None) or response.getcode()
            return RawResponse(int(status), _lowercase(response.headers), body, truncated)
    except urllib.error.HTTPError as err:
        # 3xx (not followed), 4xx and 5xx arrive here: they are answers, not failures.
        try:
            body, truncated = read_capped(err, MAX_WIRE_BYTES)
        finally:
            err.close()
        return RawResponse(int(err.code), _lowercase(err.headers or {}), body, truncated)


def urllib_transport(
    url: str, headers: Mapping[str, str], timeout: float = TIMEOUT_SECONDS
) -> RawResponse:
    """Live GET. Raises TransportError for network problems; lets real bugs through."""
    try:
        return _get(url, headers, timeout)
    except Exception as exc:
        problem = classify_exception(exc)
        if problem is None:
            raise
        raise problem from exc
