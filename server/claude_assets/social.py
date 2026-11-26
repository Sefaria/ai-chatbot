"""A small client for Sefaria Social's Claude-asset routes, used by ``save_asset``.

Social identifies the user by an identity token Sefaria issues (RS256, verified against
Sefaria's JWKS). The MCP gets one by trading its 5-minute ``mcp`` token at Sefaria's
``POST /api/social/token``; Sefaria marks it ``via: "mcp"`` and Social accepts such a token
only on ``POST /api/v1/claude-assets`` and ``/import`` (sefaria-social spec 064).

Contract coded against: sefaria-social spec 060 (draft, PR Sefaria/sefaria-social#42). Neither
the token nor the HTML body is ever logged.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("claude_assets")

TIMEOUT_SECONDS = 30.0
SAVE_PATH = "/api/v1/claude-assets"


def social_api_base() -> str | None:
    """``SOCIAL_API_BASE_URL`` (Social's origin, e.g. ``https://social.sefaria.org``), or None."""
    base = (os.environ.get("SOCIAL_API_BASE_URL") or "").strip().rstrip("/")
    return base or None


@dataclass
class SocialResponse:
    status: int
    body: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def _json_or_text(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


async def post_asset(
    base: str, token: str, payload: dict[str, Any], *, http: httpx.AsyncClient
) -> SocialResponse:
    """``POST {base}/api/v1/claude-assets``. Raises ValueError when Social cannot be reached."""
    try:
        response = await http.post(
            f"{base}{SAVE_PATH}",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "social_asset_unreachable dry_run=%s error=%s",
            payload.get("dryRun"),
            type(exc).__name__,
        )
        raise ValueError(
            f"Sefaria Social could not be reached ({type(exc).__name__}). Nothing was saved; "
            "try again in a minute."
        ) from exc
    logger.info(
        "social_asset_post dry_run=%s status=%s", payload.get("dryRun"), response.status_code
    )
    return SocialResponse(response.status_code, _json_or_text(response))


# Problem codes from spec 060 whose message needs a hint about what to do next.
_HINTS = {
    "asset.daily_limit": "Saving is limited per day; try again tomorrow.",
    "asset.refs_unavailable": "Sefaria could not check the refs just now; try again in a few minutes.",
    "asset.not_found": "Leave out asset_id to save it as a new asset, or use the id of one of your own assets.",
    "auth.mcp_not_allowed": "This connector may only save assets.",
}


def describe_error(result: SocialResponse) -> str:
    """Social's refusal as text a model can act on: the message, then each problem."""
    body = result.body if isinstance(result.body, dict) else {}
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    message = (
        (
            error.get("message")
            if isinstance(error, dict)
            else (error if isinstance(error, str) else None)
        )
        or body.get("message")
        or body.get("detail")
    )
    if not message:
        text = result.body if isinstance(result.body, str) else ""
        message = text.strip()[:300] or f"HTTP {result.status}"
    lines = [f"{message}" + (f" ({code})" if code else "")]
    for problem in body.get("problems") or []:
        if not isinstance(problem, dict):
            continue
        tag = f"[{problem['code']}] " if problem.get("code") else ""
        ref = f" (ref: {problem['ref']})" if problem.get("ref") else ""
        lines.append(f"- {tag}{problem.get('message') or ''}{ref}".rstrip())
    if code in _HINTS:
        lines.append(_HINTS[code])
    if result.status in (401, 403) and code not in _HINTS:
        lines.append("Reconnect the Sefaria connector and try again.")
    return "\n".join(lines)
