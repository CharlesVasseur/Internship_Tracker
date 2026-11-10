"""Save every response to disk, and replay it later without any network (step T2.3).

Why: when we improve an analyzer we must not contact a firm again. A live run
with a cache writes every answer; a replay run reads them back.

One JSON file per request:  <folder>/<host>/<hash of the url>.json

    {"version": 1, "url": ..., "fetched_at": ..., "kind": "response",
     "status": 200, "headers": {...}, "body_b64": "...", "truncated": false}

    {"version": 1, "url": ..., "fetched_at": ..., "kind": "error",
     "error_kind": "tls", "error_message": "..."}

The body is stored as it came on the wire (still gzip if it was), so replaying
runs the decompression and charset code again: fixing a decoder is testable too.
Failures (timeout, tls, dns...) are stored as well, so a replay is faithful.

Pure Python standard library. No network access in this file.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from tracker.triage.contract import utc_now_iso
from tracker.triage.transport import TRANSPORT_KINDS, RawResponse, Transport, TransportError

CACHE_VERSION = 1
DEFAULT_CACHE_DIR = "triage_cache"


class CacheError(Exception):
    """A cache file could not be written, or exists but cannot be trusted."""


class CacheMiss(Exception):
    """Replay was asked for a URL that was never saved."""


@dataclass(frozen=True)
class CacheEntry:
    url: str
    fetched_at: str
    response: RawResponse | None = None
    error: TransportError | None = None


class ResponseCache:
    def __init__(self, folder: str | Path = DEFAULT_CACHE_DIR) -> None:
        self.folder = Path(folder)

    # -- where a URL lives -------------------------------------------------------

    def path_for(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return self.folder / _host_folder(url) / f"{digest}.json"

    # -- writing -----------------------------------------------------------------------

    def put_response(self, url: str, raw: RawResponse, fetched_at: str) -> None:
        self._write(url, {
            "kind": "response", "fetched_at": fetched_at, "status": raw.status,
            "headers": dict(raw.headers),
            "body_b64": base64.b64encode(raw.body).decode("ascii"),
            "truncated": raw.truncated,
        })

    def put_error(self, url: str, error: TransportError, fetched_at: str) -> None:
        self._write(url, {
            "kind": "error", "fetched_at": fetched_at,
            "error_kind": error.kind, "error_message": error.message,
        })

    def _write(self, url: str, fields: dict) -> None:
        path = self.path_for(url)
        data = {"version": CACHE_VERSION, "url": url, **fields}
        temp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, path)          # all or nothing: no half-written entry
        except OSError as exc:
            raise CacheError(f"cannot write cache entry {path}: {exc}") from exc

    # -- reading --------------------------------------------------------------------------

    def get(self, url: str) -> CacheEntry | None:
        """None if the URL was never saved. CacheError if a file exists but is broken."""
        path = self.path_for(url)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as exc:
            raise CacheError(f"cannot read cache entry {path}: {exc}") from exc
        try:
            return _entry_from_json(url, text)
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            raise CacheError(f"cache entry {path} is not valid: {exc}") from exc


def _entry_from_json(url: str, text: str) -> CacheEntry:
    data = json.loads(text)
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
        raise ValueError("unknown cache format or version")
    if data["url"] != url:
        raise ValueError(f"entry belongs to another url: {data['url']!r}")
    fetched_at = data["fetched_at"]
    if not isinstance(fetched_at, str):
        raise TypeError("fetched_at must be text")

    if data["kind"] == "response":
        headers = data["headers"]
        if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
        ):
            raise TypeError("headers must map text to text")
        if not isinstance(data["status"], int) or not isinstance(data["truncated"], bool):
            raise TypeError("status must be a number and truncated a true/false value")
        body = base64.b64decode(data["body_b64"], validate=True)
        return CacheEntry(url, fetched_at, response=RawResponse(
            data["status"], headers, body, data["truncated"]))

    if data["kind"] == "error":
        if data["error_kind"] not in TRANSPORT_KINDS or not isinstance(data["error_message"], str):
            raise ValueError("bad error entry")
        return CacheEntry(url, fetched_at, error=TransportError(
            data["error_kind"], data["error_message"]))

    raise ValueError(f"unknown entry kind {data['kind']!r}")


def _host_folder(url: str) -> str:
    """A safe folder name from the host: letters, digits, dots and dashes only."""
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    cleaned = re.sub(r"[^a-z0-9.-]", "_", host.lower()).strip(".")
    return cleaned or "_nohost"      # also stops names like ".." from leaving the folder


# ---------------------------------------------------------------------------
# Two transports that plug the cache in
# ---------------------------------------------------------------------------


class RecordingTransport:
    """Wraps the live transport and saves every answer and every failure."""

    def __init__(
        self, inner: Transport, cache: ResponseCache, clock: Callable[[], str] = utc_now_iso
    ) -> None:
        self.inner = inner
        self.cache = cache
        self.clock = clock

    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> RawResponse:
        try:
            raw = self.inner(url, headers, timeout)
        except TransportError as error:
            self.cache.put_error(url, error, self.clock())
            raise
        self.cache.put_response(url, raw, self.clock())
        return raw


class ReplayTransport:
    """Answers only from the cache. It cannot reach the network: it has no way to."""

    def __init__(self, cache: ResponseCache) -> None:
        self.cache = cache

    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> RawResponse:
        entry = self.cache.get(url)
        if entry is None:
            raise CacheMiss(f"replay: {url} is not in the cache")
        if entry.error is not None:
            raise entry.error
        assert entry.response is not None
        return entry.response
