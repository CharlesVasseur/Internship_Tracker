"""Read robots.txt and decide whether a URL may be fetched (step T2.2).

Why our own matcher: Python's urllib.robotparser reads wildcards literally, so
Milliman's "Allow: */JobBoard/" never matched and the whole site looked
disallowed. This module follows the standard (RFC 9309) instead:

    * a rule is a path pattern; "*" matches any characters, "$" at the end
      means "the URL must end here"
    * of all rules that match a URL, the LONGEST pattern wins
    * on a tie, Allow wins
    * no matching rule means allowed

Which group of rules applies to us: the group naming our product token
(for example "InternshipTracker"); if there is none, the "*" group; if there is
no "*" group either, there are no rules.

Unclear files are never read as permission: a non-empty body with no robots
directive at all (an HTML error page, for example) is "unreadable" and
allowed() returns False for every URL.

Known limits (not handled, written down so they are not a surprise):
percent-encoding is compared as written, and only the directives user-agent,
allow, disallow, crawl-delay and sitemap are read. Rules for other crawlers are
ignored.

Pure Python standard library. No network access in this file.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from functools import lru_cache

MAX_CRAWL_DELAY_SECONDS = 10.0   # a longer Crawl-delay is not waited for (adjustable)
MIN_SECONDS_PER_HOST = 1.0       # at most 1 request per second per host

_RECOGNISED = ("user-agent", "allow", "disallow", "crawl-delay", "sitemap")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Rule:
    allow: bool
    pattern: str


@dataclass(frozen=True)
class RobotsRules:
    """What robots.txt says about us. Build it with parse_robots()."""

    rules: tuple[Rule, ...] = ()
    crawl_delay: float | None = None
    sitemaps: tuple[str, ...] = ()        # the Sitemap: lines, as written (not fetched)
    matched_group: str = ""               # our token, "*", or "" when no group applies
    readable: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def allow_all(cls) -> RobotsRules:
        """For a robots.txt that does not exist (404): no rules, everything allowed."""
        return cls(notes=("no robots.txt: no rules",))

    def allowed(self, url: str) -> bool:
        if not self.readable:
            return False
        target = _target_of(url)
        if target is None:
            return False
        if target.split("?", 1)[0] == "/robots.txt":
            return True
        best: tuple[int, bool] | None = None      # (pattern length, is_allow)
        for rule in self.rules:
            if _regex_for(rule.pattern).match(target):
                key = (len(rule.pattern), rule.allow)   # tie: True > False, so Allow wins
                if best is None or key > best:
                    best = key
        return True if best is None else best[1]

    def describe(self) -> str:
        """One short line for evidence."""
        if not self.readable:
            return "robots.txt unreadable: " + "; ".join(self.notes)
        group = f"group {self.matched_group!r}" if self.matched_group else "no group for us"
        delay = f", Crawl-delay {self.crawl_delay:g}s" if self.crawl_delay is not None else ""
        return f"{group}, {len(self.rules)} rule(s){delay}, {len(self.sitemaps)} Sitemap line(s)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def agent_token(user_agent: str) -> str:
    """'InternshipTracker/0.1 (triage; contact ...)' -> 'internshiptracker'."""
    first = user_agent.strip().split("/", 1)[0].split(" ", 1)[0]
    return first.lower()


def wait_seconds(
    crawl_delay: float | None,
    min_interval: float = MIN_SECONDS_PER_HOST,
    cap: float = MAX_CRAWL_DELAY_SECONDS,
) -> tuple[float, bool]:
    """Seconds to wait between requests to a host, and whether the delay is over the cap.

    The wait is the larger of 1 second and the Crawl-delay. If the Crawl-delay is
    over the cap, the caller must not wait: it reports failed/budget instead.
    """
    delay = crawl_delay or 0.0
    return max(min_interval, delay), delay > cap


def _target_of(url: str) -> str | None:
    """The part of the URL that rules are matched against: path plus query."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    path = parts.path or "/"
    return path + ("?" + parts.query if parts.query else "")


@lru_cache(maxsize=512)
def _regex_for(pattern: str) -> re.Pattern[str]:
    """Pattern -> regex, matched from the start. '*' = anything, trailing '$' = end."""
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    body = ".*".join(re.escape(piece) for piece in pattern.split("*"))
    return re.compile(body + ("$" if anchored else ""))


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    delays: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


def parse_robots(text: str, token: str = "internshiptracker") -> RobotsRules:
    """Parse a robots.txt body for the agent `token` (lowercase product name)."""
    groups: list[_Group] = []
    sitemaps: list[str] = []
    notes: list[str] = []
    current: _Group | None = None
    collecting_agents = False     # True while we are still reading User-agent lines
    has_content = False
    recognised = 0

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()       # drop comments
        if not line:
            continue                               # blank lines do not end a group
        has_content = True
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        name, value = name.strip().lower(), value.strip()
        if name not in _RECOGNISED:
            continue
        recognised += 1

        if name == "sitemap":
            if value:
                sitemaps.append(value)
        elif name == "user-agent":
            if current is None or not collecting_agents:
                current = _Group()
                groups.append(current)
            collecting_agents = True
            agent = value.lower().split("/", 1)[0].strip()
            if agent:
                current.agents.append(agent)
        else:   # allow, disallow, crawl-delay
            if current is None:
                _note(notes, f"{name} line before any User-agent line was ignored")
                continue
            collecting_agents = False
            if name == "crawl-delay":
                number = _NUMBER.match(value)
                if number:
                    current.delays.append(float(number.group()))
                else:
                    _note(notes, f"Crawl-delay value {value!r} was not understood and ignored")
            else:
                _add_rule(current, notes, allow=(name == "allow"), pattern=value)

    if has_content and recognised == 0:
        return RobotsRules(
            readable=False,
            notes=("no robots directive found in a non-empty body (an HTML page?)",),
        )

    selected = [g for g in groups if token in g.agents]
    matched = token
    if not selected:
        selected = [g for g in groups if "*" in g.agents]
        matched = "*"
    if not selected:
        matched = ""

    rules = tuple(rule for g in selected for rule in g.rules)
    delays = [d for g in selected for d in g.delays]
    return RobotsRules(
        rules=rules,
        crawl_delay=max(delays) if delays else None,   # several values: the larger, to be polite
        sitemaps=tuple(sitemaps),
        matched_group=matched,
        readable=True,
        notes=tuple(notes),
    )


def _add_rule(group: _Group, notes: list[str], *, allow: bool, pattern: str) -> None:
    if not pattern:
        return    # an empty Disallow means "nothing is disallowed"; an empty Allow means nothing
    if not pattern.startswith(("/", "*")):
        if allow:
            _note(notes, f"Allow pattern {pattern!r} has no leading '/' and was ignored")
            return
        _note(notes, f"Disallow pattern {pattern!r} has no leading '/' and was read as '/{pattern}'")
        pattern = "/" + pattern       # a missing slash must never make us less careful
    group.rules.append(Rule(allow, pattern))


def _note(notes: list[str], message: str) -> None:
    if message not in notes and len(notes) < 5:
        notes.append(message)
