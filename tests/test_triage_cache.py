"""Tests for src/tracker/triage/cache.py (step T2.3). Offline, files in a temp folder."""

import json

import pytest

from tracker.triage.cache import (
    CacheEntry,
    CacheError,
    CacheMiss,
    RecordingTransport,
    ReplayTransport,
    ResponseCache,
)
from tracker.triage.transport import RawResponse, TransportError

URL = "https://careers.x.test/jobs?page=1"
NOW = "2026-11-10T12:00:00+00:00"
RAW = RawResponse(200, {"content-type": "text/html; charset=utf-8", "content-encoding": "gzip"},
                  b"\x1f\x8b\x00binary\xff", False)


@pytest.fixture
def cache(tmp_path):
    return ResponseCache(tmp_path / "triage_cache")


# -- round trips --------------------------------------------------------------------------------------

def test_a_response_round_trips_exactly_including_binary_bytes(cache):
    cache.put_response(URL, RAW, NOW)
    entry = cache.get(URL)
    assert entry == CacheEntry(URL, NOW, response=RAW)


def test_a_truncated_flag_survives(cache):
    cache.put_response(URL, RawResponse(200, {}, b"abc", True), NOW)
    assert cache.get(URL).response.truncated is True


def test_a_failure_round_trips(cache):
    cache.put_error(URL, TransportError("tls", "certificate verify failed"), NOW)
    entry = cache.get(URL)
    assert entry.response is None
    assert (entry.error.kind, entry.error.message) == ("tls", "certificate verify failed")


def test_a_url_never_saved_is_a_plain_none(cache):
    assert cache.get(URL) is None


def test_saving_again_replaces_the_old_entry(cache):
    cache.put_error(URL, TransportError("timeout", "slow"), NOW)
    cache.put_response(URL, RAW, NOW)
    assert cache.get(URL).response == RAW


def test_accents_in_headers_and_urls_survive(cache):
    url = "https://x.test/offres?q=stagiaire-é"
    raw = RawResponse(200, {"x-note": "Crédit"}, "é".encode(), False)
    cache.put_response(url, raw, NOW)
    assert cache.get(url).response == raw


# -- where files go ---------------------------------------------------------------------------------------

def test_one_file_per_url_under_a_host_folder(cache):
    cache.put_response(URL, RAW, NOW)
    path = cache.path_for(URL)
    assert path.exists() and path.parent.name == "careers.x.test" and path.suffix == ".json"


def test_different_urls_get_different_files_and_the_same_url_the_same_file(cache):
    other = "https://careers.x.test/jobs?page=2"
    assert cache.path_for(URL) != cache.path_for(other)
    assert cache.path_for(URL) == cache.path_for(URL)


def test_odd_hosts_cannot_escape_the_cache_folder(cache):
    for url in ("http://../x", "http://a.test:8080/x", "http://[::1]/x", "not a url", ""):
        path = cache.path_for(url)
        assert cache.folder in path.parents


def test_the_port_is_not_part_of_the_folder_name(cache):
    assert cache.path_for("http://a.test:8080/x").parent.name == "a.test"


def test_no_temporary_files_are_left_behind(cache):
    cache.put_response(URL, RAW, NOW)
    assert [p.name for p in cache.path_for(URL).parent.iterdir()] == [cache.path_for(URL).name]


def test_the_file_is_readable_json(cache):
    cache.put_response(URL, RAW, NOW)
    data = json.loads(cache.path_for(URL).read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["url"] == URL and data["kind"] == "response"


# -- broken files are visible, not silent ------------------------------------------------------------------

def broken(cache, text):
    path = cache.path_for(URL)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("text", [
    "{not json",
    "[]",
    json.dumps({"version": 99, "url": URL}),
    json.dumps({"version": 1, "url": "https://other.test/", "kind": "error",
                "fetched_at": NOW, "error_kind": "tls", "error_message": "x"}),
    json.dumps({"version": 1, "url": URL, "kind": "response", "fetched_at": NOW,
                "status": 200, "headers": {}, "body_b64": "!!!not base64!!!", "truncated": False}),
    json.dumps({"version": 1, "url": URL, "kind": "response", "fetched_at": NOW,
                "status": "200", "headers": {}, "body_b64": "", "truncated": False}),
    json.dumps({"version": 1, "url": URL, "kind": "error", "fetched_at": NOW,
                "error_kind": "mystery", "error_message": "x"}),
    json.dumps({"version": 1, "url": URL, "kind": "mystery", "fetched_at": NOW}),
    json.dumps({"version": 1, "url": URL, "kind": "response"}),
])
def test_a_broken_entry_raises_cache_error(cache, text):
    broken(cache, text)
    with pytest.raises(CacheError):
        cache.get(URL)


def test_an_unwritable_folder_raises_cache_error(tmp_path):
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    with pytest.raises(CacheError):
        ResponseCache(blocker / "cache").put_response(URL, RAW, NOW)


# -- recording and replay ------------------------------------------------------------------------------------

class Scripted:
    """A tiny fake live transport: returns or raises what it is given, counts calls."""

    def __init__(self, result):
        self.result, self.calls = result, 0

    def __call__(self, url, headers, timeout):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_recording_saves_an_answer_and_passes_it_through(cache):
    recorder = RecordingTransport(Scripted(RAW), cache, clock=lambda: NOW)
    assert recorder(URL, {}, 15) == RAW
    assert cache.get(URL) == CacheEntry(URL, NOW, response=RAW)


def test_recording_saves_a_failure_and_raises_it_again(cache):
    recorder = RecordingTransport(Scripted(TransportError("dns", "no such host")), cache, clock=lambda: NOW)
    with pytest.raises(TransportError):
        recorder(URL, {}, 15)
    assert cache.get(URL).error.kind == "dns"


def test_replay_gives_back_exactly_what_was_recorded(cache):
    RecordingTransport(Scripted(RAW), cache, clock=lambda: NOW)(URL, {}, 15)
    assert ReplayTransport(cache)(URL, {}, 15) == RAW


def test_replay_raises_the_recorded_failure(cache):
    RecordingTransport(Scripted(TransportError("tls", "bad cert")), cache)  # not called
    cache.put_error(URL, TransportError("tls", "bad cert"), NOW)
    with pytest.raises(TransportError) as info:
        ReplayTransport(cache)(URL, {}, 15)
    assert (info.value.kind, info.value.message) == ("tls", "bad cert")


def test_replay_of_an_unknown_url_is_a_cache_miss(cache):
    with pytest.raises(CacheMiss) as info:
        ReplayTransport(cache)(URL, {}, 15)
    assert URL in str(info.value)


def test_replay_never_calls_a_live_transport(cache):
    live = Scripted(RAW)
    RecordingTransport(live, cache)(URL, {}, 15)
    assert live.calls == 1
    ReplayTransport(cache)(URL, {}, 15)
    ReplayTransport(cache)(URL, {}, 15)
    assert live.calls == 1
