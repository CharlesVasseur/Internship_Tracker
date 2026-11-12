"""Read the parts of a page that the analyzers need (steps T3.1 and T3.2).

Uses Python's built-in html.parser, which is forgiving: broken HTML never makes
it raise. We only look at the raw HTML as the server sent it. JavaScript is not
run, so a page built in the browser can look empty here, and that is exactly the
"app shell" case we want to recognise.

Collected:
    hrefs          every <a href="..."> exactly as written (not yet resolved)
    resource_urls  (tag, url) for <link href>, <script src> and <form action>
    jsonld_blocks  the text of every <script type="application/ld+json">
    feed_links     (url, type) for <link rel="alternate"> with an RSS or Atom type
    empty_root     True if the page has a <div id="root"> with nothing inside it

Pure Python standard library. No network access in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

# Ids of the empty container that a JavaScript app fills in. Only "root" has been
# seen on a real capture. Other names are added only when a real captured page
# shows them: the spec forbids guessing page structures.
SHELL_ROOT_IDS = ("root",)

FEED_TYPES = ("application/rss+xml", "application/atom+xml")


@dataclass(frozen=True)
class PageScan:
    hrefs: tuple[str, ...] = ()
    resource_urls: tuple[tuple[str, str], ...] = ()
    jsonld_blocks: tuple[str, ...] = ()
    feed_links: tuple[tuple[str, str], ...] = ()
    empty_root: bool = False


class _Scanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)   # &amp; and friends are decoded for us
        self.hrefs: list[str] = []
        self.resource_urls: list[tuple[str, str]] = []
        self.jsonld_blocks: list[str] = []
        self.feed_links: list[tuple[str, str]] = []
        self.empty_root = False
        self._inside_root = False
        self._root_has_content = False
        self._root_seen = False
        self._jsonld: list[str] | None = None      # text pieces of the JSON-LD block being read

    def handle_starttag(self, tag, attrs):
        attributes = {name: (value or "") for name, value in attrs}
        if self._inside_root:
            self._root_has_content = True          # any tag inside the container is content
        elif tag == "div" and not self._root_seen:
            if attributes.get("id", "").strip().lower() in SHELL_ROOT_IDS:
                self._inside_root = True
                self._root_seen = True
                self._root_has_content = False

        if tag == "a":
            href = attributes.get("href", "").strip()
            if href:
                self.hrefs.append(href)
        elif tag == "link":
            href = attributes.get("href", "").strip()
            if href:
                self.resource_urls.append(("link", href))
                kind = attributes.get("type", "").split(";")[0].strip().lower()
                relations = attributes.get("rel", "").lower().split()
                if "alternate" in relations and kind in FEED_TYPES:
                    self.feed_links.append((href, kind))
        elif tag == "script":
            source = attributes.get("src", "").strip()
            if source:
                self.resource_urls.append(("script", source))
            if attributes.get("type", "").split(";")[0].strip().lower() == "application/ld+json":
                self._jsonld = []
        elif tag == "form":
            action = attributes.get("action", "").strip()
            if action:
                self.resource_urls.append(("form", action))

    def handle_data(self, data):
        if self._jsonld is not None:
            self._jsonld.append(data)
        if self._inside_root and data.strip():
            self._root_has_content = True

    def handle_endtag(self, tag):
        if tag == "script" and self._jsonld is not None:
            self.jsonld_blocks.append("".join(self._jsonld))
            self._jsonld = None
        if self._inside_root and tag == "div":
            self._inside_root = False
            self.empty_root = not self._root_has_content


def scan_html(text: str) -> PageScan:
    scanner = _Scanner()
    # A page cut short (for example at the size cap) can end inside a <script>. Some
    # Python versions give the parser's pending script text to us and some do not,
    # so we close any open script ourselves. When no script is open, the extra end
    # tag is an orphan that the scanner ignores.
    scanner.feed(text + "</script>")
    scanner.close()
    if scanner._jsonld is not None:                # a JSON-LD script that was never closed
        scanner.jsonld_blocks.append("".join(scanner._jsonld))
    return PageScan(
        tuple(scanner.hrefs), tuple(scanner.resource_urls), tuple(scanner.jsonld_blocks),
        tuple(scanner.feed_links), scanner.empty_root,
    )
