"""Tests for src/tracker/triage/transport.py (step T2.3). Offline.

The live request itself is never made here. The opener is replaced by a fake, so
we test what we wrote: reading, capping, header handling and error typing.
The SSL message in the tls test is the real one from the survey (SNB, Vaudoise).
"""

import http.client
import io
import re
import socket
import ssl
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

from conftest import NetworkAttempt
from tracker.triage import transport as transport_module
from tracker.triage.transport import (
    ContactRequired,
    RawResponse,
    TransportError,
    _NoRedirect,
    build_user_agent,
    classify_exception,
    read_capped,
    request_headers,
    urllib_transport,
)

URL = "https://x.test/jobs"
SELF_SIGNED = ssl.SSLCertVerificationError(
    1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
       "self-signed certificate in certificate chain (_ssl.c:1000)")


# -- the network guard itself -----------------------------------------------------

def test_the_network_guard_stops_real_connections():
    with pytest.raises(NetworkAttempt):
        socket.create_connection(("example.com", 80))
    with pytest.raises(NetworkAttempt):
        socket.getaddrinfo("example.com", 80)


def test_the_guard_cannot_be_swallowed_by_except_exception():
    assert not issubclass(NetworkAttempt, Exception)


# -- user agent and headers ------------------------------------------------------------

def test_user_agent_names_the_project_and_carries_the_contact():
    agent = build_user_agent("me@example.com", environ={})
    assert agent == "InternshipTracker/0.1 (triage; contact me@example.com)"


def test_contact_can_come_from_the_environment():
    assert "env@example.com" in build_user_agent(None, environ={"SURVEY_CONTACT": "env@example.com"})


def test_the_argument_beats_the_environment():
    agent = build_user_agent("arg@example.com", environ={"SURVEY_CONTACT": "env@example.com"})
    assert "arg@example.com" in agent and "env@example.com" not in agent


@pytest.mark.parametrize("bad", [None, "", "   ", "no-address-here"])
def test_a_live_run_refuses_to_start_without_a_contact(bad):
    with pytest.raises(ContactRequired):
        build_user_agent(bad, environ={})


def test_a_contact_page_url_is_accepted_too():
    assert "https://me.example/contact" in build_user_agent("https://me.example/contact", environ={})


def test_no_contact_address_is_stored_in_the_source():
    source = Path(transport_module.__file__).read_text(encoding="utf-8")
    addresses = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", source)
    assert set(addresses) <= {"you@example.com"}      # only the example in the help text


def test_request_headers_are_only_agent_and_encoding():
    headers = request_headers("InternshipTracker/0.1 (x)")
    assert headers == {"User-Agent": "InternshipTracker/0.1 (x)", "Accept-Encoding": "gzip, deflate"}


# -- error typing -------------------------------------------------------------------------

@pytest.mark.parametrize("exc, kind", [
    (SELF_SIGNED, "tls"),
    (urllib.error.URLError(SELF_SIGNED), "tls"),
    (ssl.SSLError("handshake failure"), "tls"),
    (socket.gaierror(-2, "Name or service not known"), "dns"),
    (urllib.error.URLError(socket.gaierror(-2, "Name or service not known")), "dns"),
    (TimeoutError("timed out"), "timeout"),
    (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
    (urllib.error.URLError("timed out"), "timeout"),
    (ConnectionRefusedError("refused"), "connection"),
    (urllib.error.URLError(ConnectionRefusedError("refused")), "connection"),
    (urllib.error.URLError("something odd"), "connection"),
    (http.client.RemoteDisconnected("closed"), "connection"),
    (http.client.IncompleteRead(b"abc"), "connection"),
    (OSError("network unreachable"), "connection"),
    (http.client.InvalidURL("bad port"), "invalid_url"),
    (urllib.error.URLError("unknown url type: ftp"), "invalid_url"),
    (ValueError("bad host"), "invalid_url"),
    (UnicodeError("label too long"), "invalid_url"),
])
def test_exceptions_become_typed_errors(exc, kind):
    problem = classify_exception(exc)
    assert isinstance(problem, TransportError)
    assert problem.kind == kind


def test_the_tls_message_keeps_the_reason():
    assert "self-signed certificate" in classify_exception(SELF_SIGNED).message


def test_a_real_bug_is_not_turned_into_a_network_error():
    assert classify_exception(KeyError("oops")) is None
    assert classify_exception(AttributeError("oops")) is None


def test_unknown_transport_error_kind_is_refused():
    with pytest.raises(ValueError):
        TransportError("mystery", "x")


# -- fake opener for urllib_transport ----------------------------------------------------------

class FakeResponse(io.BytesIO):
    def __init__(self, body=b"", status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


class FakeOpener:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def use(monkeypatch, result):
    opener = FakeOpener(result)
    monkeypatch.setattr(transport_module, "_build_opener", lambda: opener)
    return opener


def http_error(code, body=b"", **headers):
    message = Message()
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    return urllib.error.HTTPError(URL, code, "msg", message, io.BytesIO(body))


def test_a_normal_answer_is_returned_with_lowercase_headers(monkeypatch):
    opener = use(monkeypatch, FakeResponse(b"<html>", 200, {"Content-Type": "text/html"}))
    raw = urllib_transport(URL, {"User-Agent": "T"}, 15)
    assert raw == RawResponse(200, {"content-type": "text/html"}, b"<html>", False)
    request, timeout = opener.requests[0]
    assert request.get_method() == "GET" and request.full_url == URL
    assert request.get_header("User-agent") == "T" and timeout == 15


def test_http_errors_are_answers_not_failures(monkeypatch):
    use(monkeypatch, http_error(404, b"gone", Content_Type="text/html"))
    raw = urllib_transport(URL, {}, 15)
    assert (raw.status, raw.body, raw.headers["content-type"]) == (404, b"gone", "text/html")


def test_a_redirect_is_handed_back_with_its_location(monkeypatch):
    use(monkeypatch, http_error(301, b"", Location="https://y.test/new"))
    raw = urllib_transport(URL, {}, 15)
    assert (raw.status, raw.headers["location"]) == (301, "https://y.test/new")


@pytest.mark.parametrize("code", [401, 403, 429, 500, 503])
def test_blocking_and_server_codes_are_answers_too(monkeypatch, code):
    use(monkeypatch, http_error(code))
    assert urllib_transport(URL, {}, 15).status == code


def test_the_opener_refuses_to_follow_redirects():
    handler = _NoRedirect()
    assert handler.redirect_request(None, None, 301, "moved", {}, "https://y.test/") is None


def test_a_body_over_the_cap_is_cut_and_flagged(monkeypatch):
    monkeypatch.setattr(transport_module, "MAX_WIRE_BYTES", 10)
    use(monkeypatch, FakeResponse(b"x" * 25))
    raw = urllib_transport(URL, {}, 15)
    assert raw.truncated is True
    assert len(raw.body) == 10


def test_network_problems_raise_a_typed_error(monkeypatch):
    use(monkeypatch, urllib.error.URLError(SELF_SIGNED))
    with pytest.raises(TransportError) as info:
        urllib_transport(URL, {}, 15)
    assert info.value.kind == "tls"


def test_a_timeout_while_reading_the_body_is_a_timeout(monkeypatch):
    class Slow(FakeResponse):
        def read(self, size=-1):
            raise TimeoutError("timed out")
    use(monkeypatch, Slow())
    with pytest.raises(TransportError) as info:
        urllib_transport(URL, {}, 15)
    assert info.value.kind == "timeout"


def test_a_bug_is_not_hidden_by_the_transport(monkeypatch):
    use(monkeypatch, KeyError("bug"))
    with pytest.raises(KeyError):
        urllib_transport(URL, {}, 15)


# -- read_capped ----------------------------------------------------------------------------------

def test_read_capped_exactly_at_the_limit_is_not_truncated():
    assert read_capped(io.BytesIO(b"a" * 10), 10) == (b"a" * 10, False)


def test_read_capped_one_byte_over_is_truncated():
    assert read_capped(io.BytesIO(b"a" * 11), 10) == (b"a" * 10, True)


def test_read_capped_empty_body():
    assert read_capped(io.BytesIO(b""), 10) == (b"", False)


# -- verification is never turned off ------------------------------------------------------------------

def test_the_source_never_switches_certificate_checking_off():
    source = Path(transport_module.__file__).read_text(encoding="utf-8")
    for forbidden in ("CERT_NONE", "_create_unverified_context", "check_hostname = False",
                      "verify=False", "_create_default_https_context"):
        assert forbidden not in source
