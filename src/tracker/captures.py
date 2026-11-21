"""Offline helpers for listing, showing and exporting cached triage responses."""

from __future__ import annotations

import base64
import binascii
import json
import re
import urllib.parse
import zlib
from dataclasses import dataclass
from pathlib import Path

from tracker.triage.decode import _BodyProblem, decode_body, decompress


@dataclass(frozen=True)
class CaptureEntry:
    """One valid cache file, ready for filtering and display."""

    file_name: str
    url: str
    host: str
    fetched_at: str
    kind: str
    status: int | None = None
    headers: dict[str, str] | None = None
    body: bytes | None = None
    truncated: bool = False
    error_kind: str | None = None


@dataclass(frozen=True)
class CaptureResult:
    """A small typed result for commands that can fail."""

    ok: bool
    text: str = ""
    body: bytes | None = None
    warnings: tuple[str, ...] = ()


def _lower_headers(headers: dict[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


def read_cache(cache_dir: str | Path) -> tuple[list[CaptureEntry], list[str]]:
    """Read every valid cache file; bad files become warnings, never exceptions."""
    folder = Path(cache_dir)
    entries: list[CaptureEntry] = []
    warnings: list[str] = []
    if not folder.is_dir():
        return entries, [f"cache folder not found: {folder}"]

    files = sorted(folder.rglob("*.json"), key=lambda path: str(path).lower())
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1:
                raise ValueError("unknown cache format or version")
            url = data["url"]
            fetched_at = data["fetched_at"]
            if not isinstance(url, str) or not isinstance(fetched_at, str):
                raise ValueError("url and fetched_at must be text")
            host = urllib.parse.urlsplit(url).hostname or ""
            host = host.lower()
            kind = data["kind"]
            if kind == "error":
                error_kind = data["error_kind"]
                if not isinstance(error_kind, str):
                    raise ValueError("error_kind must be text")
                entries.append(CaptureEntry(
                    path.name, url, host, fetched_at, kind, error_kind=error_kind,
                ))
            elif kind == "response":
                status = data["status"]
                headers = data["headers"]
                truncated = data["truncated"]
                if not isinstance(status, int):
                    raise ValueError("status must be a number")
                if not isinstance(headers, dict) or not all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in headers.items()
                ):
                    raise ValueError("headers must map text to text")
                if not isinstance(truncated, bool):
                    raise ValueError("truncated must be true or false")
                body = base64.b64decode(data["body_b64"], validate=True)
                entries.append(CaptureEntry(
                    path.name, url, host, fetched_at, kind,
                    status=status, headers=_lower_headers(headers), body=body,
                    truncated=truncated,
                ))
            else:
                raise ValueError(f"unknown cache entry kind: {kind}")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError,
                TypeError, ValueError, binascii.Error) as error:
            warnings.append(f"Warning: cannot read cache file {path.name}: {error}")

    entries.sort(key=lambda entry: (entry.host, entry.url))
    return entries, warnings


def filter_entries(
    entries: list[CaptureEntry],
    host: str | None = None,
    url_contains: str | None = None,
) -> list[CaptureEntry]:
    """Filter entries by host and URL, both case-insensitively."""
    host_need = host.lower() if host is not None else None
    url_need = url_contains.lower() if url_contains is not None else None
    return [
        entry for entry in entries
        if (host_need is None or host_need in entry.host)
        and (url_need is None or url_need in entry.url.lower())
    ]


def format_entry(index: int, entry: CaptureEntry) -> str:
    """Format one list line without ever printing a body."""
    if entry.kind == "error":
        status = entry.error_kind or "error"
    else:
        status = str(entry.status)
    headers = entry.headers or {}
    content_type = headers.get("content-type", "")
    if entry.body is not None:
        try:
            decoded_size = len(decompressed_body(entry))
        except (_BodyProblem, zlib.error, ValueError, OverflowError):
            decoded_size = 0
    else:
        decoded_size = 0
    url = entry.url if len(entry.url) <= 150 else entry.url[:150]
    return f"{index} {status} {content_type} {decoded_size} {url}"


def decompressed_body(entry: CaptureEntry) -> bytes:
    """Return the body after gzip or deflate decompression."""
    if entry.body is None:
        raise ValueError("entry has no body")
    headers = entry.headers or {}
    return decompress(
        entry.body,
        headers.get("content-encoding", ""),
        partial=True,
        wire_truncated=entry.truncated,
    )[0]


def select_entry(
    entries: list[CaptureEntry],
    url_contains: str,
    host: str | None = None,
    index: int | None = None,
) -> tuple[CaptureEntry | None, str | None, list[CaptureEntry]]:
    """Apply show/export selection rules and return an entry or an error."""
    filtered = filter_entries(entries, host=host, url_contains=url_contains)
    if not filtered:
        return None, f"Error: no cache entry matches URL containing {url_contains!r}", filtered
    if len(filtered) > 1 and index is None:
        lines = ["Error: multiple cache entries match; use --index N"]
        for number, entry in enumerate(filtered):
            lines.append(format_entry(number, entry))
        return None, "\n".join(lines), filtered
    if index is not None:
        if index < 0 or index >= len(filtered):
            return None, f"Error: --index {index} is out of range", filtered
        return filtered[index], None, filtered
    return filtered[0], None, filtered


def decoded_text(entry: CaptureEntry) -> CaptureResult:
    """Decode one response body for show operations."""
    if entry.body is None:
        return CaptureResult(False, "Error: cache entry has a transport error and no body")
    result = decode_body(entry.body, entry.headers or {}, wire_truncated=entry.truncated)
    if result.error_kind is not None:
        return CaptureResult(False, f"Error: could not decode body ({result.error_kind}: {result.error_message})")
    return CaptureResult(True, text=result.text or "")


def show_head(entry: CaptureEntry, count: int) -> CaptureResult:
    """Return the first N characters of decoded text."""
    if count <= 0:
        return CaptureResult(False, "Error: --head count must be greater than zero")
    decoded = decoded_text(entry)
    if not decoded.ok:
        return decoded
    return CaptureResult(True, text=decoded.text[:count])


def find_locs(entry: CaptureEntry, count: int) -> CaptureResult:
    """Return the first N text values between <loc> tags."""
    if count <= 0:
        return CaptureResult(False, "Error: --locs count must be greater than zero")
    decoded = decoded_text(entry)
    if not decoded.ok:
        return decoded
    values = re.findall(r"<loc(?:\s[^>]*)?>(.*?)</loc\s*>", decoded.text, re.I | re.S)
    return CaptureResult(True, text="\n".join(value.strip() for value in values[:count]))


def find_jsonld(entry: CaptureEntry) -> CaptureResult:
    """Return JSON-LD script blocks with a separator line between blocks."""
    decoded = decoded_text(entry)
    if not decoded.ok:
        return decoded
    pattern = re.compile(
        r"<script\b[^>]*\btype\s*=\s*[\"']application/ld\+json[\"'][^>]*>"
        r"(.*?)</script\s*>",
        re.I | re.S,
    )
    blocks = [match.group(1).strip()[:4000] for match in pattern.finditer(decoded.text)]
    return CaptureResult(True, text="\n\n---\n\n".join(blocks))


def find_links(entry: CaptureEntry, count: int, contains: str | None = None) -> CaptureResult:
    """List matching anchor hrefs and visible text, with one summary warning."""
    if count <= 0:
        return CaptureResult(False, "Error: --links count must be greater than zero")
    decoded = decoded_text(entry)
    if not decoded.ok:
        return decoded

    anchor_pattern = re.compile(r"<a\b([^>]*)>(.*?)</a\s*>", re.I | re.S)
    href_pattern = re.compile(r"\bhref\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.I)
    tag_pattern = re.compile(r"<[^>]*>", re.S)
    need = contains.lower() if contains is not None else None
    matched = 0
    seen: set[str] = set()
    lines: list[str] = []

    for anchor in anchor_pattern.finditer(decoded.text):
        href_match = href_pattern.search(anchor.group(1))
        if href_match is None:
            continue
        href = href_match.group(1) if href_match.group(1) is not None else href_match.group(2)
        if need is not None and need not in href.lower():
            continue
        matched += 1
        if href in seen:
            continue
        seen.add(href)
        if len(lines) < count:
            visible = tag_pattern.sub("", anchor.group(2))
            visible = " ".join(visible.split())
            if not visible:
                visible = "(no text)"
            if len(visible) > 120:
                visible = visible[:120]
            lines.append(f"{href}\t{visible}")

    summary = f"{matched} anchors matched"
    return CaptureResult(True, text="\n".join(lines), warnings=(summary,))


def find_around(
    entry: CaptureEntry,
    text: str,
    max_count: int = 3,
    chars: int = 400,
) -> CaptureResult:
    """Return raw-text snippets around literal, case-insensitive matches."""
    if max_count <= 0:
        return CaptureResult(False, "Error: --max count must be greater than zero")
    if chars <= 0:
        return CaptureResult(False, "Error: --chars count must be greater than zero")
    if not text:
        return CaptureResult(False, "Error: --around TEXT cannot be empty")
    decoded = decoded_text(entry)
    if not decoded.ok:
        return decoded

    source = decoded.text
    source_folded = source.lower()
    needle = text.lower()
    positions: list[int] = []
    start = 0
    while len(positions) < max_count:
        position = source_folded.find(needle, start)
        if position < 0:
            break
        positions.append(position)
        start = position + 1

    if not positions:
        return CaptureResult(False, f"Error: text not found: {text}")

    snippets: list[str] = []
    for position in positions:
        snippet_start = max(0, position - chars)
        snippet_end = min(len(source), position + len(text) + chars)
        snippets.append(source[snippet_start:snippet_end])
    return CaptureResult(True, text="\n-----\n".join(snippets))


def export_entry(entry: CaptureEntry, out_path: str | Path, force: bool = False) -> CaptureResult:
    """Write decompressed bytes and a small whitelist-only metadata file."""
    if entry.body is None:
        return CaptureResult(False, "Error: cache entry has a transport error and no body")

    fixture_root = Path(__file__).resolve().parents[2] / "tests" / "local_fixtures"
    fixture_root = fixture_root.resolve()
    output = Path(out_path).resolve()
    try:
        output.relative_to(fixture_root)
    except ValueError:
        return CaptureResult(False, f"Error: output path must be inside {fixture_root}")

    meta = Path(str(output) + ".meta.json")
    if not force and (output.exists() or meta.exists()):
        return CaptureResult(False, f"Error: output file already exists: {output}; use --force")

    try:
        body = decompressed_body(entry)
    except (_BodyProblem, zlib.error, ValueError, OverflowError) as error:
        return CaptureResult(False, f"Error: could not decompress body: {error}")

    headers = entry.headers or {}
    whitelisted: dict[str, str] = {}
    for name in ("content-type", "last-modified", "etag", "content-language"):
        if name in headers:
            whitelisted[name] = headers[name]
    metadata = {
        "url": entry.url,
        "fetched_at": entry.fetched_at,
        "status": entry.status,
        "headers": whitelisted,
    }
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(body)
        meta.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        return CaptureResult(False, f"Error: could not write export: {error}")
    return CaptureResult(True)
