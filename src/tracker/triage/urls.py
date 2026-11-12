"""One small URL helper shared by the analyzers and the fetcher.

urllib.parse.urljoin raises ValueError for some broken addresses (for example
"http://[bad"). A page or a redirect header we do not control must never be able
to crash a check, so every place that resolves an address found in a page uses
this wrapper instead.

Pure Python standard library.
"""

from __future__ import annotations

import urllib.parse


def resolve_url(base: str, reference: str, drop_fragment: bool = False) -> str | None:
    """The absolute address, or None if `reference` cannot be resolved. Never raises."""
    try:
        absolute = urllib.parse.urljoin(base, reference)
        return urllib.parse.urldefrag(absolute)[0] if drop_fragment else absolute
    except ValueError:
        return None
