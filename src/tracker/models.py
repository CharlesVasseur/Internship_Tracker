"""Shared vocabulary for tracker records."""

APPLICATION_STAGES = (
    "To research",
    "Contacted",
    "Applied",
    "Online assessment",
    "Online interview",
    "In-person interview",
    "Offer",
    "Rejected",
)

OPPORTUNITY_STATES = (
    "Open",
    "Upcoming",
    "Closed",
    "No match",
    "Needs review",
)

MANUAL_PREFIX = "manual:"

ACTIVITY_CREATED = "application_created"
ACTIVITY_STAGE_CHANGE = "stage_change"
ACTIVITY_NOTE = "note"

ACTIVITY_TYPES = (
    ACTIVITY_CREATED,
    ACTIVITY_STAGE_CHANGE,
    ACTIVITY_NOTE,
)

LINK_TYPES = ("search_link", "direct_posting", "careers_page")
