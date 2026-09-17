"""Client-initiated conversation flows.

A *flow* is a conversation the reader enters deliberately from the site — today
only "Report an issue" on an unverified machine translation — rather than one
the router classifies from the message text.

When a flow is set, its prompt is authoritative and the router is skipped
entirely. That matters on every turn, not just the first: the router classifies
only the latest user message, so without the skip, turn three of a report
conversation would silently land on a different prompt mid-argument.

Unknown flow values fail open (treated as no flow, router runs as usual), so a
stale or malformed client cannot disable routing by accident.
"""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger("chat.flows")

REPORT_ISSUE = "report_issue"

# Flow name -> the settings attribute holding its Braintrust prompt slug. Resolved
# lazily against settings so tests can override the slug.
_FLOW_PROMPT_SETTING = {
    REPORT_ISSUE: "REPORT_ISSUE_PROMPT_SLUG",
}


def normalize_flow(raw_flow: object) -> str | None:
    """Coerce a client-supplied flow value to a known flow name, or None.

    Returns None for anything unrecognised — missing, blank, wrong type, or a
    flow this server doesn't know about.
    """
    if not isinstance(raw_flow, str):
        return None
    flow = raw_flow.strip()
    if not flow:
        return None
    if flow not in _FLOW_PROMPT_SETTING:
        logger.warning("Unknown flow %r requested; falling back to router", flow[:50])
        return None
    return flow


def get_flow_prompt_slug(flow: str | None) -> str | None:
    """Return the Braintrust prompt slug for `flow`, or None if it has no prompt."""
    setting_name = _FLOW_PROMPT_SETTING.get(flow or "")
    if not setting_name:
        return None
    slug = getattr(settings, setting_name, "") or ""
    if not slug:
        logger.error("Flow %r has no prompt slug configured (%s)", flow, setting_name)
        return None
    return slug
