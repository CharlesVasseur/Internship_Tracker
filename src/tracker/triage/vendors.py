"""Spot recruiting-system hostnames on a page (step T3.2).

Many firms run their careers site on a vendor's platform. The hostname of that
platform is a CLUE about how the firm could be monitored: it is never proof, and
the spec says so (verdict "probable", not "automatic").

The table below only holds hostnames that were actually observed in the survey
of 19 firms. Each entry says how it was verified, using the survey's own source
labels: script = the survey script's output, log = its run log, manual = the
manual survey, saved page = a page saved from the browser. Nothing is added from
general knowledge: a new vendor goes in only when a real capture shows it.

Matching is by host LABEL: "x.y.myworkdayjobs.com" has the label "myworkdayjobs".
This also covers the same vendor under another country ending. The places we
look are the ones the survey used: anchors, <link href>, <script src>,
<form action>, plus the page's own address.

Pure Python standard library. No network access in this file.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from tracker.triage.contract import Evidence
from tracker.triage.html_scan import PageScan, scan_html
from tracker.triage.urls import resolve_url

MAX_VENDOR_EVIDENCE = 6


@dataclass(frozen=True)
class Vendor:
    name: str
    label: str        # a host label to look for
    verified: str     # how the hostname was seen in the survey


VENDORS = (
    Vendor("Workday", "myworkdayjobs", "survey: script output and a saved page"),
    Vendor("Oracle Cloud", "oraclecloud", "survey: script output and manual survey"),
    Vendor("Phenom", "phenompeople", "survey: script output (phenompeople hosts)"),
    Vendor("UltiPro", "ultipro", "survey: manual survey"),
    Vendor("csod", "csod", "survey: run log"),
    Vendor("softgarden", "softgarden", "survey: run log and a saved page"),
    Vendor("yello", "yello", "survey: run log"),
    Vendor("SuccessFactors", "successfactors", "survey: a saved page"),
    Vendor("icims", "icims", "survey: manual survey (apply links)"),
)


def vendor_for_host(host: str) -> Vendor | None:
    labels = host.lower().split(".")
    for vendor in VENDORS:
        if vendor.label in labels:
            return vendor
    return None


def find_vendors(
    page_url: str, scan: PageScan, extra: tuple[tuple[str, str], ...] = ()
) -> list[Evidence]:
    """One vendor_host evidence per distinct (vendor, host), in the order first seen.

    `extra` holds more (where, url) pairs the caller wants checked, for example
    ("example job URL", "https://...").
    """
    places: list[tuple[str, str]] = [("page address", page_url)]
    places += [(tag, url) for tag, url in scan.resource_urls]
    places += [("anchor", url) for url in scan.hrefs]
    places += list(extra)

    seen: set[tuple[str, str]] = set()
    evidence: list[Evidence] = []
    for where, url in places:
        absolute = resolve_url(page_url, url)
        if absolute is None:
            continue
        try:
            host = urllib.parse.urlsplit(absolute).hostname
        except ValueError:
            continue
        vendor = vendor_for_host(host or "")
        if vendor is None or (vendor.name, host) in seen:
            continue
        seen.add((vendor.name, host))
        evidence.append(Evidence.of("vendor_host", page_url, f"{vendor.name}: {host} (seen in {where})"))
        if len(evidence) >= MAX_VENDOR_EVIDENCE:
            break
    return evidence


def analyze_vendors(
    page_url: str, page_text: str, extra: tuple[tuple[str, str], ...] = ()
) -> list[Evidence]:
    return find_vendors(page_url, scan_html(page_text), extra)
