"""Turn the bytes of a response into text (step T2.1).

Three jobs, in this order:

    1. Is the body text at all?  (a PDF is "not_text", not a decode failure)
    2. Undo gzip / deflate, with a size cap so a "zip bomb" cannot eat memory.
    3. Find a charset that works, and record which one was used and why.

`decode_body` never raises. It returns a DecodedBody that either holds text or
holds an error kind and a message. The fetcher (T2.4) turns that into a
TriageError. Error kinds used here: "not_text", "too_large", "decode", and "internal" for our own bugs.

An unknown charset name (for example "ISO-8852", seen at Banque de France) is
skipped, never fatal: we move on to the next candidate and write it down.

Pure Python standard library. No network access in this file.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass

MAX_BODY_BYTES = 2 * 1024 * 1024   # the size cap, also applied after decompression
SNIFF_BYTES = 4096                 # how much of the start we read to find <meta charset>

# Content types we are willing to read as text.
_TEXT_TYPES = frozenset({
    "application/xhtml+xml", "application/xml", "application/json",
    "application/ld+json", "application/rss+xml", "application/atom+xml",
})

_BOMS = (
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)

_HEADER_CHARSET = re.compile(r"charset\s*=\s*[\"']?([^\s;\"']+)", re.I)
_META_CHARSET = re.compile(r"<meta[^>]+charset\s*=\s*[\"']?([A-Za-z0-9_.:\-]+)", re.I)


@dataclass(frozen=True)
class DecodedBody:
    """Text, or the reason there is none. Exactly one of the two is set."""

    text: str | None = None
    charset_used: str | None = None
    charset_source: str | None = None   # header / bom / meta / default-utf8 / fallback-cp1252
    charset_note: str | None = None     # what was skipped or approximated, if anything
    error_kind: str | None = None       # not_text / too_large / decode / internal
    error_message: str | None = None

    def __post_init__(self) -> None:
        if (self.text is None) == (self.error_kind is None):
            raise ValueError("a DecodedBody holds either text or an error, not both or neither")


def _fail(kind: str, message: str) -> DecodedBody:
    return DecodedBody(error_kind=kind, error_message=message)


# ---------------------------------------------------------------------------
# 1. Is it text?
# ---------------------------------------------------------------------------


def is_text_type(content_type: str) -> bool:
    """True for text/*, XML, JSON and friends. A missing Content-Type counts as text."""
    main = content_type.split(";", 1)[0].strip().lower()
    if not main:
        return True
    return main.startswith("text/") or main in _TEXT_TYPES or main.endswith(("+xml", "+json"))


# ---------------------------------------------------------------------------
# 2. Decompression
# ---------------------------------------------------------------------------


class _BodyProblem(Exception):
    """Internal: carries an error kind and message up to decode_body. Never escapes it."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _inflate(data: bytes, wbits: int, limit: int) -> bytes:
    """Decompress with a cap. wbits: 31 = gzip, 15 = zlib-wrapped deflate, -15 = raw deflate."""
    engine = zlib.decompressobj(wbits)
    try:
        out = engine.decompress(data, limit + 1)   # at most limit+1 bytes come out
    except zlib.error as exc:
        raise _BodyProblem("decode", f"cannot decompress body: {exc}") from exc
    if len(out) > limit:
        raise _BodyProblem("too_large", f"body is larger than {limit} bytes after decompression")
    if not engine.eof:
        raise _BodyProblem("decode", "compressed body is cut short (stream not finished)")
    return out


def decompress(body: bytes, content_encoding: str, limit: int = MAX_BODY_BYTES) -> bytes:
    """Undo Content-Encoding. Raises _BodyProblem; only decode_body calls this."""
    encoding = content_encoding.strip().lower()
    if encoding in ("", "identity"):
        if len(body) > limit:
            raise _BodyProblem("too_large", f"body is larger than {limit} bytes")
        return body
    if encoding in ("gzip", "x-gzip"):
        return _inflate(body, 31, limit)
    if encoding == "deflate":
        # Servers disagree: some send zlib-wrapped data, some raw deflate. Try both.
        try:
            return _inflate(body, 15, limit)
        except _BodyProblem as first:
            if first.kind == "too_large":
                raise
            return _inflate(body, -15, limit)
    raise _BodyProblem("decode", f"unsupported Content-Encoding {content_encoding!r}")


# ---------------------------------------------------------------------------
# 3. Charset
# ---------------------------------------------------------------------------


def _header_charset(content_type: str) -> str | None:
    match = _HEADER_CHARSET.search(content_type)
    return match.group(1) if match else None


def _bom_charset(body: bytes) -> str | None:
    for mark, name in _BOMS:
        if body.startswith(mark):
            return name
    return None


def _meta_charset(body: bytes) -> str | None:
    head = body[:SNIFF_BYTES].decode("latin-1")   # latin-1 never fails; we only read ASCII
    match = _META_CHARSET.search(head)
    if match:
        return match.group(1)
    # <meta http-equiv="Content-Type" content="text/html; charset=windows-1252">
    return _header_charset(head) if "http-equiv" in head.lower() else None


def decode_text(body: bytes, content_type: str = "") -> DecodedBody:
    """Find a charset that works. Order: header, BOM, <meta>, UTF-8, cp1252."""
    candidates: list[tuple[str, str]] = []   # (source, charset name)
    for source, name in (
        ("header", _header_charset(content_type)),
        ("bom", _bom_charset(body)),
        ("meta", _meta_charset(body)),
        ("default-utf8", "utf-8"),
    ):
        if name:
            candidates.append((source, name))

    skipped: list[str] = []
    tried: set[str] = set()
    for source, name in candidates:
        key = name.lower()
        if key in tried:          # do not try the same charset twice
            continue
        tried.add(key)
        try:
            text = body.decode(name)
        except LookupError:       # unknown name, or a codec that is not a text encoding
            skipped.append(f"{source} charset {name!r} is unknown")
            continue
        except UnicodeDecodeError:
            skipped.append(f"{source} charset {name!r} could not decode the body")
            continue
        return DecodedBody(
            text=text.lstrip("\ufeff"), charset_used=name, charset_source=source,
            charset_note="; ".join(skipped) or None,
        )

    # Last resort: cp1252, which is what old Western European pages usually are.
    try:
        text = body.decode("cp1252")
        note = "; ".join(skipped + ["used cp1252 as a fallback"])
    except UnicodeDecodeError:
        text = body.decode("cp1252", errors="replace")
        count = text.count("\ufffd")
        note = "; ".join(skipped + [f"used cp1252 as a fallback with {count} replaced character(s)"])
    return DecodedBody(
        text=text.lstrip("\ufeff"), charset_used="cp1252", charset_source="fallback-cp1252",
        charset_note=note,
    )


# ---------------------------------------------------------------------------
# The one function the fetcher calls
# ---------------------------------------------------------------------------


def decode_body(
    body: bytes, headers: dict[str, str], limit: int = MAX_BODY_BYTES
) -> DecodedBody:
    """Bytes plus headers in, text or a typed problem out. Never raises.

    `headers` is read case-insensitively.
    """
    try:
        lowered = {key.lower(): value for key, value in headers.items()}
        content_type = lowered.get("content-type", "")
        if not is_text_type(content_type):
            main = content_type.split(";", 1)[0].strip()
            return _fail("not_text", f"Content-Type is {main!r}, which is not text")
        raw = decompress(body, lowered.get("content-encoding", ""), limit)
        return decode_text(raw, content_type)
    except _BodyProblem as problem:
        return _fail(problem.kind, problem.message)
    except Exception as exc:   # a bug in this file: report it, do not crash the run
        return _fail("internal", f"unexpected {type(exc).__name__} while decoding: {exc}")
