"""Adapter contracts and registered source adapters."""

from tracker.adapters.base import AdapterResult, Listing, validate_result
from tracker.adapters.paged_cards import ADAPTER_NAME, check_paged_cards

ADAPTERS = {ADAPTER_NAME: check_paged_cards}

__all__ = (
    "Listing",
    "AdapterResult",
    "validate_result",
    "ADAPTERS",
)
