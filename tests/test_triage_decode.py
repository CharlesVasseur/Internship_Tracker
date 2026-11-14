"""Tests for src/tracker/triage/decode.py (step T2.1). Offline.

All byte strings here are SYNTHETIC: tiny made-up bodies that test the decoding
rules. They are not copies of any real page. The charset name ISO-8852 is a real
value observed at Banque de France (survey report, section 6).
"""

import gzip
import random
import zlib

import pytest

from tracker.triage.contract import ERROR_KINDS
from tracker.triage.decode import DecodedBody, decode_body, is_text_type

HTML = {"content-type": "text/html; charset=utf-8"}


# -- is it text? ---------------------------------------------------------------

@pytest.mark.parametrize("ctype", [
    "text/html", "text/plain; charset=utf-8", "application/xml", "application/json",
    "application/rss+xml", "image/svg+xml", "application/ld+json", "", "TEXT/HTML",
])
def test_text_types_are_accepted(ctype):
    assert is_text_type(ctype)


@pytest.mark.parametrize("ctype", ["application/pdf", "image/png", "application/octet-stream"])
def test_other_types_are_not_text(ctype):
    assert not is_text_type(ctype)


def test_pdf_is_not_text_and_says_so():
    result = decode_body(b"%PDF-1.7 ...", {"Content-Type": "application/pdf"})
    assert result.text is None
    assert result.error_kind == "not_text"
    assert "application/pdf" in result.error_message


def test_missing_content_type_is_read_as_text():
    assert decode_body(b"hello", {}).text == "hello"


def test_header_names_are_case_insensitive():
    assert decode_body(b"caf\xe9", {"CONTENT-TYPE": "text/html; charset=latin-1"}).text == "café"


# -- charset ------------------------------------------------------------------

def test_header_charset_is_used_and_not_called_a_fallback():
    result = decode_body("café".encode("utf-8"), HTML)
    assert result.text == "café"
    assert (result.charset_used, result.charset_source, result.charset_note) == ("utf-8", "header", None)


def test_header_latin1_decodes_latin1_bytes():
    result = decode_body(b"caf\xe9", {"content-type": "text/html; charset=ISO-8859-1"})
    assert result.text == "café"
    assert result.charset_source == "header"


def test_unknown_header_charset_does_not_fail_the_page():
    """ISO-8852 does not exist. The page must still be read, and the skip recorded."""
    result = decode_body("café".encode("utf-8"), {"content-type": "text/html; charset=ISO-8852"})
    assert result.error_kind is None
    assert result.text == "café"
    assert result.charset_source == "default-utf8"
    assert "ISO-8852" in result.charset_note


def test_non_text_codec_name_is_treated_as_unknown():
    result = decode_body(b"hello", {"content-type": "text/html; charset=base64"})
    assert result.text == "hello"
    assert "base64" in result.charset_note


def test_utf8_bom_is_used_and_removed():
    result = decode_body(b"\xef\xbb\xbfcaf\xc3\xa9", {"content-type": "text/html"})
    assert result.text == "café"
    assert result.charset_source == "bom"


def test_utf16_bom():
    result = decode_body("café".encode("utf-16"), {"content-type": "text/html"})
    assert result.text == "café"
    assert result.charset_source == "bom"


def test_meta_charset():
    body = b'<html><head><meta charset="iso-8859-1"></head><body>caf\xe9</body></html>'
    result = decode_body(body, {"content-type": "text/html"})
    assert "café" in result.text
    assert result.charset_source == "meta"


def test_meta_http_equiv_charset():
    body = (b'<meta http-equiv="Content-Type" content="text/html; charset=windows-1252">'
            b"caf\xe9")
    result = decode_body(body, {"content-type": "text/html"})
    assert result.text.endswith("café")
    assert result.charset_source == "meta"


def test_nothing_declared_and_valid_utf8_is_the_default_without_a_note():
    result = decode_body("café".encode("utf-8"), {"content-type": "text/html"})
    assert (result.charset_source, result.charset_note) == ("default-utf8", None)


def test_invalid_utf8_falls_back_to_cp1252_and_records_it():
    result = decode_body(b"caf\xe9", {"content-type": "text/html"})
    assert result.text == "café"
    assert result.charset_used == "cp1252"
    assert result.charset_source == "fallback-cp1252"
    assert "cp1252" in result.charset_note


def test_header_says_utf8_but_bytes_are_not_records_the_failed_header():
    result = decode_body(b"caf\xe9", HTML)
    assert result.text == "café"
    assert result.charset_source == "fallback-cp1252"
    assert "header" in result.charset_note and "utf-8" in result.charset_note


def test_cp1252_undefined_byte_is_replaced_and_counted_not_fatal():
    result = decode_body(b"ab\x81cd\xe9", {"content-type": "text/html"})
    assert result.error_kind is None
    assert "\ufffd" in result.text
    assert "1 replaced" in result.charset_note


# -- decompression ---------------------------------------------------------------

def test_gzip_body():
    headers = {**HTML, "content-encoding": "gzip"}
    assert decode_body(gzip.compress("héllo".encode()), headers).text == "héllo"


def test_deflate_zlib_wrapped():
    headers = {**HTML, "content-encoding": "deflate"}
    assert decode_body(zlib.compress(b"hello"), headers).text == "hello"


def test_deflate_raw():
    engine = zlib.compressobj(wbits=-15)
    raw = engine.compress(b"hello") + engine.flush()
    assert decode_body(raw, {**HTML, "content-encoding": "deflate"}).text == "hello"


def test_corrupt_gzip_is_a_decode_error():
    result = decode_body(b"not gzip at all", {**HTML, "content-encoding": "gzip"})
    assert result.error_kind == "decode"


def test_truncated_gzip_is_a_decode_error():
    cut = gzip.compress(b"hello world " * 100)[:-10]
    result = decode_body(cut, {**HTML, "content-encoding": "gzip"})
    assert result.error_kind == "decode"
    assert "cut short" in result.error_message


def test_unsupported_encoding_is_a_decode_error():
    result = decode_body(b"x", {**HTML, "content-encoding": "br"})
    assert result.error_kind == "decode"
    assert "br" in result.error_message


# -- size cap -----------------------------------------------------------------------

def test_body_over_the_cap_is_too_large():
    result = decode_body(b"a" * 101, HTML, limit=100)
    assert result.error_kind == "too_large"


def test_body_exactly_at_the_cap_is_fine():
    assert decode_body(b"a" * 100, HTML, limit=100).text == "a" * 100


def test_small_gzip_that_expands_past_the_cap_is_too_large():
    bomb = gzip.compress(b"a" * 5000)
    assert len(bomb) < 100
    result = decode_body(bomb, {**HTML, "content-encoding": "gzip"}, limit=100)
    assert result.error_kind == "too_large"


# -- never raises, always a valid shape --------------------------------------------------

def test_every_error_kind_is_in_the_contract():
    samples = [
        decode_body(b"%PDF", {"content-type": "application/pdf"}),
        decode_body(b"a" * 5, HTML, limit=1),
        decode_body(b"x", {**HTML, "content-encoding": "gzip"}),
    ]
    for item in samples:
        assert item.error_kind in ERROR_KINDS


def test_random_garbage_never_raises():
    rng = random.Random(42)
    encodings = ["", "gzip", "deflate", "identity", "br", "x-gzip"]
    types = ["text/html", "text/html; charset=ISO-8852", "application/pdf", "", "text/plain; charset=utf-16"]
    for _ in range(300):
        body = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 60)))
        headers = {"content-type": rng.choice(types), "content-encoding": rng.choice(encodings)}
        result = decode_body(body, headers)
        assert isinstance(result, DecodedBody)
        assert (result.text is None) != (result.error_kind is None)


def test_a_decoded_body_cannot_hold_both_text_and_error():
    with pytest.raises(ValueError):
        DecodedBody(text="x", error_kind="decode", error_message="y")
    with pytest.raises(ValueError):
        DecodedBody()


# -- partial mode: keep the start of a body that is too big or cut short ---------------------------------------------

def test_partial_mode_keeps_the_first_bytes_of_an_oversized_plain_body():
    result = decode_body(b"abcdefghij" * 20, HTML, limit=100, partial=True)
    assert result.text == ("abcdefghij" * 10) and result.truncated is True and result.error_kind is None


def test_the_default_mode_is_unchanged_and_never_reports_truncation():
    assert decode_body(b"a" * 101, HTML, limit=100).error_kind == "too_large"
    assert decode_body(b"a" * 100, HTML, limit=100).truncated is False


def test_a_body_under_the_limit_is_not_truncated_even_in_partial_mode():
    result = decode_body(b"hello", HTML, limit=100, partial=True)
    assert result.text == "hello" and result.truncated is False


def test_partial_mode_keeps_the_start_of_a_gzip_body_that_expands_past_the_limit():
    result = decode_body(gzip.compress(b"a" * 5000), {**HTML, "content-encoding": "gzip"}, limit=100, partial=True)
    assert result.text == "a" * 100 and result.truncated is True


def test_partial_mode_reads_a_compressed_body_cut_short_as_far_as_it_goes():
    text = "".join(f"line number {n}\n" for n in range(2000))
    cut = gzip.compress(text.encode())[:400]
    headers = {**HTML, "content-encoding": "gzip"}
    result = decode_body(cut, headers, partial=True, wire_truncated=True)
    assert result.truncated is True and result.text.startswith("line number 0\n") and 0 < len(result.text) < len(text)
    assert decode_body(cut, headers).error_kind == "decode"          # the default mode still refuses


def test_partial_mode_works_for_both_kinds_of_deflate():
    text = "".join(f"line number {n}\n" for n in range(2000)).encode()
    wrapped = zlib.compress(text)[:300]
    engine = zlib.compressobj(wbits=-15)
    raw = (engine.compress(text) + engine.flush())[:300]
    for body in (wrapped, raw):
        result = decode_body(body, {**HTML, "content-encoding": "deflate"}, partial=True, wire_truncated=True)
        assert result.truncated is True and result.text.startswith("line number 0\n")


def test_a_character_cut_in_two_at_the_end_does_not_send_the_page_to_cp1252():
    body = ("é" * 100).encode("utf-8")                    # 200 bytes
    result = decode_body(body, {"content-type": "text/html"}, limit=101, partial=True)
    assert result.text == "é" * 50 and result.truncated is True
    assert (result.charset_used, result.charset_source) == ("utf-8", "default-utf8")


def test_an_odd_cut_in_utf16_is_dropped_too():
    body = "ab".encode("utf-16")                          # BOM + 2 characters
    result = decode_body(body + b"\x00" * 3, {"content-type": "text/html"}, limit=len(body) + 1, partial=True)
    assert result.text.startswith("ab") and result.truncated is True


def test_partial_mode_never_raises_on_garbage():
    rng = random.Random(21)
    for _ in range(300):
        body = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80)))
        headers = {"content-type": rng.choice(["text/html", "text/plain; charset=ISO-8852", ""]),
                   "content-encoding": rng.choice(["", "gzip", "deflate", "br"])}
        result = decode_body(body, headers, limit=rng.choice([5, 30, 1000]), partial=True)
        assert isinstance(result, DecodedBody)
        assert (result.text is None) != (result.error_kind is None)


def test_a_gzip_stream_that_ends_early_without_a_wire_cut_is_a_broken_stream_even_in_partial_mode():
    """Partial mode forgives only a cut WE made at the cap, never a damaged download."""
    cut = gzip.compress(("line number 1\n" * 3000).encode())[:-20]
    result = decode_body(cut, {**HTML, "content-encoding": "gzip"}, partial=True)
    assert result.error_kind == "decode" and "cut short" in result.error_message


def test_a_character_cut_in_two_at_the_wire_cut_is_dropped_and_utf8_is_kept():
    body = ("é" * 10).encode("utf-8")[:-1]
    result = decode_body(body, {"content-type": "text/html"}, partial=True, wire_truncated=True)
    assert result.text == "é" * 9 and result.charset_source == "default-utf8" and result.truncated is True


def test_without_the_wire_cut_flag_the_same_bytes_fall_back_to_cp1252():
    body = ("é" * 10).encode("utf-8")[:-1]
    assert decode_body(body, {"content-type": "text/html"}, partial=True).charset_source == "fallback-cp1252"
