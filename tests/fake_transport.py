"""Test helpers for the fetcher: a scripted fake transport and a fake clock.

Not a test file. No network: the fake transport only returns what it is given.
"""

from tracker.triage.transport import RawResponse


class UnscriptedRequest(BaseException):
    """The fetcher asked for a URL the test did not expect.

    A BaseException on purpose: the fetcher catches `Exception` to survive bugs,
    and that must never hide an unexpected request from the test.
    """


class FakeTransport:
    """Returns scripted answers per URL, in order; the last one repeats forever.

    transport.add(url, answer1, answer2, ...)  where an answer is a RawResponse
    or an exception instance to raise (for example TransportError).
    """

    def __init__(self, clock=None):
        self.script: dict[str, list] = {}
        self.calls: list[str] = []
        self.times: list[float | None] = []
        self.sent_headers: list[dict] = []
        self.timeouts: list[float] = []
        self.clock = clock

    def add(self, url, *answers):
        self.script[url] = list(answers)
        return self

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        self.times.append(self.clock() if self.clock else None)
        self.sent_headers.append(dict(headers))
        self.timeouts.append(timeout)
        queue = self.script.get(url)
        if not queue:
            raise UnscriptedRequest(f"unexpected request to {url}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClock:
    """Time that only moves when the fetcher sleeps, so tests run instantly."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def __call__(self):
        return self.now


def reply(status=200, body="", ctype="text/html; charset=utf-8", **headers):
    """A RawResponse. Header keyword underscores become dashes (Content_Encoding)."""
    data = body.encode("utf-8") if isinstance(body, str) else body
    fields = {name.replace("_", "-").lower(): value for name, value in headers.items()}
    if ctype:
        fields["content-type"] = ctype
    return RawResponse(status, fields, data, False)


def redirect(location, status=301):
    return reply(status, "", ctype="", Location=location)


ALLOW_ALL = "User-agent: *\nDisallow:"


def robots_ok(text=ALLOW_ALL):
    return reply(200, text, ctype="text/plain; charset=utf-8")
