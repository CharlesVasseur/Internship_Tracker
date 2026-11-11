"""Read the parts of a page that the analyzers need (step T3.1).

Uses Python's built-in html.parser, which is forgiving: broken HTML never makes
it raise. We only look at the raw HTML as the server sent it. JavaScript is not
run, so a page built in the browser can look empty here, and that is exactly the
"app shell" case we want to recognise.

Collected so far (T3.1):
    hrefs       every <a href="..."> exactly as written (not yet resolved)
    empty_root  True if the page has a <div id="root"> with nothing inside it

Later steps add what they need (script and link hosts, JSON-LD, feed links).

Pure Python standard library. No network access in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

# Ids of the empty container that a JavaScript app fills in. Only "root" has been
# seen on a real capture. Other names are added only when a
# real captured page shows them: the spec forbids guessing page structures.
SHELL_ROOT_IDS = ("root",)


@dataclass(frozen=True)
class PageScan:
    hrefs: tuple[str, ...] = ()
    empty_root: bool = False


class _Scanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)   # &amp; and friends are decoded for us
        self.hrefs: list[str] = []
        self.empty_root = False
        self._inside_root = False
        self._root_has_content = False
        self._root_seen = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self._inside_root:
            self._root_has_content = True          # any tag inside the container is content
        elif tag == "div" and not self._root_seen:
            if (attributes.get("id") or "").strip().lower() in SHELL_ROOT_IDS:
                self._inside_root = True
                self._root_seen = True
                self._root_has_content = False
        if tag == "a":
            href = (attributes.get("href") or "").strip()
            if href:
                self.hrefs.append(href)

    def handle_data(self, data):
        if self._inside_root and data.strip():
            self._root_has_content = True

    def handle_endtag(self, tag):
        if self._inside_root and tag == "div":
            self._inside_root = False
            self.empty_root = not self._root_has_content


def scan_html(text: str) -> PageScan:
    scanner = _Scanner()
    scanner.feed(text)
    scanner.close()
    return PageScan(tuple(scanner.hrefs), scanner.empty_root)
