"""Offline tests for the synthetic triage capture helper."""

import gzip
import json
from pathlib import Path

from scripts.export_capture import main
from tracker.captures import read_cache, select_entry
from tracker.triage.cache import ResponseCache
from tracker.triage.transport import RawResponse, TransportError


NOW = "2026-11-20T12:00:00+00:00"
FIXTURE_ROOT = Path(__file__).resolve().parent / "local_fixtures"


def add_response(cache, url, body, status=200, headers=None, truncated=False):
    raw_headers = headers or {"content-type": "text/html; charset=utf-8"}
    ResponseCache(cache).put_response(
        url, RawResponse(status, raw_headers, body, truncated), NOW,
    )


def test_list_sorted_filters_transport_and_corrupt_cache(tmp_path, capsys):
    cache = tmp_path / "cache"
    add_response(cache, "https://b.example.test/z?q=1", b"b")
    add_response(cache, "https://a.example.test/jobs", b"a")
    ResponseCache(cache).put_error(
        "https://a.example.test/error", TransportError("tls", "bad certificate"), NOW,
    )
    corrupt = cache / "a.example.test" / "corrupt.json"
    corrupt.write_text("{broken", encoding="utf-8")

    entries, warnings = read_cache(cache)
    assert [entry.url for entry in entries] == [
        "https://a.example.test/error",
        "https://a.example.test/jobs",
        "https://b.example.test/z?q=1",
    ]
    assert warnings
    assert main(["list", "--cache-dir", str(cache)]) == 0
    output = capsys.readouterr()
    assert "tls" in output.out
    assert "Warning:" in output.err
    assert "a.example.test" in output.out
    assert main([
        "list", "--cache-dir", str(cache),
        "--host", "A.EXAMPLE", "--url-contains", "JOBS",
    ]) == 0
    assert "jobs" in capsys.readouterr().out


def test_show_locs_head_and_jsonld(tmp_path):
    cache = tmp_path / "cache"
    sitemap = b"<urlset><url><loc>https://example.test/one</loc></url>"
    sitemap += b"<url><loc>https://example.test/two</loc></url></urlset>"
    add_response(cache, "https://sitemap.example.test/feed.xml", sitemap,
                 headers={"content-type": "application/xml"})

    html = b'<script type="application/ld+json">{"name":"one"}</script>'
    html += b'<script type="application/ld+json">{"name":"two"}</script>'
    html += b"<p>no json ld here</p>"
    add_response(cache, "https://jobs.example.test/job", html)
    add_response(cache, "https://plain.example.test/page", b"<html><p>No block</p></html>")

    entries, _ = read_cache(cache)
    selected, error, _ = select_entry(entries, "feed.xml")
    assert error is None
    from tracker.captures import find_locs
    locs = find_locs(selected, 2)
    assert locs.ok
    assert locs.text == "https://example.test/one\nhttps://example.test/two"

    selected, error, _ = select_entry(entries, "/job")
    from tracker.captures import find_jsonld, show_head
    assert error is None
    blocks = find_jsonld(selected)
    assert '{"name":"one"}' in blocks.text and '{"name":"two"}' in blocks.text
    assert "\n\n---\n\n" in blocks.text
    assert show_head(selected, 12).text == '<script type'

    selected, error, _ = select_entry(entries, "plain.example")
    assert error is None
    assert find_jsonld(selected).text == ""


def test_gzip_show_and_export(tmp_path):
    cache = tmp_path / "cache"
    body = gzip.compress("Hello café".encode("utf-8"))
    add_response(cache, "https://gzip.example.test/page", body,
                 headers={"content-type": "text/plain; charset=utf-8", "content-encoding": "gzip"})
    entries, warnings = read_cache(cache)
    assert not warnings
    entry, error, _ = select_entry(entries, "page")
    assert error is None
    from tracker.captures import show_head
    assert show_head(entry, 100).text == "Hello café"

    out = FIXTURE_ROOT / "capture_gzip_test.txt"
    meta = Path(str(out) + ".meta.json")
    try:
        from tracker.captures import export_entry
        assert export_entry(entry, out).ok
        assert out.read_bytes() == "Hello café".encode("utf-8")
        assert json.loads(meta.read_text(encoding="utf-8"))["headers"]["content-type"]
    finally:
        if out.exists():
            out.unlink()
        if meta.exists():
            meta.unlink()


def test_export_meta_whitelist_and_force(tmp_path):
    cache = tmp_path / "cache"
    add_response(cache, "https://export.example.test/page", b"new body",
                 headers={
                     "content-type": "text/plain",
                     "last-modified": "Thu, 20 Nov 2026 00:00:00 GMT",
                     "etag": '"abc"',
                     "content-language": "en",
                     "set-cookie": "secret=one",
                 })
    entries, _ = read_cache(cache)
    entry, error, _ = select_entry(entries, "page")
    assert error is None

    out = FIXTURE_ROOT / "capture_export_test.txt"
    meta = Path(str(out) + ".meta.json")
    try:
        from tracker.captures import export_entry
        assert export_entry(entry, out).ok
        assert out.read_bytes() == b"new body"
        data = json.loads(meta.read_text(encoding="utf-8"))
        assert set(data) == {"url", "fetched_at", "status", "headers"}
        assert set(data["headers"]) == {
            "content-type", "last-modified", "etag", "content-language",
        }
        assert "set-cookie" not in meta.read_text(encoding="utf-8")

        assert not export_entry(entry, out).ok
        assert export_entry(entry, out, force=True).ok
    finally:
        if out.exists():
            out.unlink()
        if meta.exists():
            meta.unlink()


def test_export_refuses_outside_and_dotdot(tmp_path):
    cache = tmp_path / "cache"
    add_response(cache, "https://safe.example.test/page", b"x")
    entries, _ = read_cache(cache)
    entry, _, _ = select_entry(entries, "page")
    from tracker.captures import export_entry

    outside = tmp_path / "outside.txt"
    assert not export_entry(entry, outside).ok
    escape = FIXTURE_ROOT / ".." / ".." / "outside.txt"
    assert not export_entry(entry, escape).ok


def test_selection_ambiguity_no_match_and_index(tmp_path, capsys):
    cache = tmp_path / "cache"
    add_response(cache, "https://one.example.test/same", b"one")
    add_response(cache, "https://two.example.test/same", b"two")
    entries, _ = read_cache(cache)

    entry, error, filtered = select_entry(entries, "same")
    assert entry is None and error and "multiple" in error
    assert main([
        "show", "--cache-dir", str(cache), "--url-contains", "same", "--head", "10",
    ]) == 2
    assert "multiple" in capsys.readouterr().err

    entry, error, _ = select_entry(entries, "same", index=1)
    assert entry is not None and error is None
    assert entry.url.startswith("https://two")
    assert main([
        "show", "--cache-dir", str(cache), "--url-contains", "same",
        "--index", "1", "--head", "10",
    ]) == 0
    assert "two" in capsys.readouterr().out

    assert main([
        "show", "--cache-dir", str(cache), "--url-contains", "missing",
        "--head", "10",
    ]) == 2
    assert "no cache entry" in capsys.readouterr().err


def test_entry_without_body_cannot_show_or_export(tmp_path):
    cache = tmp_path / "cache"
    ResponseCache(cache).put_error(
        "https://error.example.test/page", TransportError("dns", "no host"), NOW,
    )
    entries, _ = read_cache(cache)
    entry, error, _ = select_entry(entries, "page")
    assert error is None
    from tracker.captures import export_entry, show_head
    assert not show_head(entry, 10).ok
    assert not export_entry(entry, FIXTURE_ROOT / "transport_error.txt").ok
