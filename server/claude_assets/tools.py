"""The ``save_asset`` tool, run by the MCP process for the signed-in user. No Django imports.

1. Trade the user's 5-minute ``mcp`` token for a Sefaria Social identity token (Sefaria).
2. Ask Social to check the file without storing it (``dryRun: true``), so a refused save
   returns every reason and leaves nothing behind.
3. Save it for real, and answer with the asset's link and a reader link beside its first ref.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from findings import refs

from . import social

MAX_BYTES = 5 * 1024 * 1024  # Social's ASSET_MAX_BYTES default (plan P5, spec 060)
MANIFEST = re.compile(r"<script\b[^>]*\bid\s*=\s*[\"']sefaria-asset[\"'][^>]*>", re.IGNORECASE)
PUBLISH_NOTE = (
    "Anyone with the link can open it. To show it in the reader's sidebar beside every passage "
    "it covers, the owner publishes it from their Sefaria Social profile, which needs a verified "
    "profile (the blue check)."
)


def sefaria_web_url() -> str:
    return (os.environ.get("SEFARIA_WEB_URL") or "https://www.sefaria.org").rstrip("/")


def reader_link(first_ref: str | None, asset_id: str, version_id: str | None = None) -> str | None:
    """A reader link that opens the asset beside its first ref, linked to it.

    The reader's form (reader-next, Sefaria-Project branch feat/reader-linked-panels): the text is
    panel 1, the asset panel 2 (``a2=<id>[@<version>]``), and ``link=2-1`` links the asset to the text.
    """
    if not first_ref:
        return None
    asset = f"{asset_id}@{version_id}" if version_id else asset_id
    return (
        f"{sefaria_web_url()}/{quote(refs.to_url(first_ref), safe='.,_-:')}"
        f"?a2={quote(asset, safe='@')}&link=2-1"
    )


def _check_arguments(arguments: dict[str, Any]) -> tuple[str, str | None, str | None]:
    html = arguments.get("html")
    if not isinstance(html, str) or not html.strip():
        raise ValueError("`html` must be the whole HTML file, manifest included.")
    size = len(html.encode("utf-8"))
    if size > MAX_BYTES:
        raise ValueError(
            f"The file is {size:,} bytes; Sefaria keeps assets up to {MAX_BYTES:,} bytes. Make it "
            "smaller (inline less data, compress images) and try again."
        )
    if not MANIFEST.search(html):
        raise ValueError(
            'The file has no manifest. Add <script type="application/json" id="sefaria-asset"> with '
            '{"v": 1, "title", "description", "refs": [{"ref", "label"}]}, checking each ref with '
            "validate_refs first (see the sefaria-linked-asset skill)."
        )
    asset_id = (arguments.get("asset_id") or "").strip() or None
    source_url = (arguments.get("source_url") or "").strip() or None
    if source_url and urlsplit(source_url).scheme != "https":
        raise ValueError(
            "`source_url` must be an https link (the Claude artifact's link), or left out."
        )
    return html, asset_id, source_url


def _first_ref(*bodies: Any) -> str | None:
    for body in bodies:
        if not isinstance(body, dict):
            continue
        first = body.get("firstRef")
        if isinstance(first, dict):
            first = first.get("ref")
        if isinstance(first, str) and first:
            return first
        manifest = body.get("manifest") or (body.get("asset") or {}).get("manifest") or {}
        for entry in manifest.get("refs") or []:
            ref = entry.get("ref") if isinstance(entry, dict) else None
            if ref:
                return ref
    return None


def _version_of(asset: dict, version: dict) -> dict:
    """The asset's entry for the saved version (it carries the public ``contentUrl``)."""
    for v in asset.get("versions") or []:
        if isinstance(v, dict) and v.get("id") == version.get("id"):
            return {**v, **version}
    return version


async def save_asset(
    sefaria_client: Any, arguments: dict[str, Any], *, http: httpx.AsyncClient | None = None
) -> dict:
    """Save ``arguments['html']`` to Sefaria Social as the signed-in user."""
    if not getattr(sefaria_client, "user_id", None):
        raise ValueError("Sign in with your Sefaria account to save assets.")
    base = social.social_api_base()
    if not base:
        raise ValueError(
            "Saving to Sefaria is not set up on this server. Download the file and upload it on "
            "your Sefaria Social profile instead."
        )
    html, asset_id, source_url = _check_arguments(arguments)

    token = await sefaria_client.mint_social_token()
    payload: dict[str, Any] = {"html": html}
    if asset_id:
        payload["assetId"] = asset_id
    if source_url:
        payload["sourceUrl"] = source_url

    own = http is None
    http = http or httpx.AsyncClient(timeout=social.TIMEOUT_SECONDS)
    try:
        dry = await social.post_asset(base, token, {**payload, "dryRun": True}, http=http)
        if not dry.ok:
            raise ValueError(
                "The asset was not saved. Fix this and call save_asset again:\n"
                + social.describe_error(dry)
            )
        saved = await social.post_asset(base, token, payload, http=http)
    finally:
        if own:
            await http.aclose()
    if not saved.ok:
        raise ValueError("The asset was not saved:\n" + social.describe_error(saved))

    body = saved.body if isinstance(saved.body, dict) else {}
    asset = body.get("asset") or {}
    new_id = str(asset.get("id") or asset_id or "")
    if not new_id:
        raise ValueError(
            "Sefaria Social answered without an asset id; the save may not have completed."
        )
    version = _version_of(asset, body.get("version") or {})
    first_ref = _first_ref(body, dry.body)
    asset_url = version.get("contentUrl") or f"{base}{social.SAVE_PATH}/{quote(new_id)}"
    reader_url = reader_link(first_ref, new_id, version.get("id"))
    manifest = asset.get("manifest") or {}
    title = asset.get("title") or manifest.get("title") or ""

    what = "unchanged (the same file is already saved)" if body.get("unchanged") else "saved"
    message = f"{title or 'The asset'} is {what}"
    if version.get("n"):
        message += f" as version {version['n']}"
    message += f". Open it: {asset_url}."
    if reader_url:
        message += f" Open it beside {first_ref} in the reader, linked to the text: {reader_url}."
    return {
        "saved": True,
        "unchanged": bool(body.get("unchanged")),
        "asset": {
            "id": new_id,
            "title": title,
            "versionId": version.get("id"),
            "version": version.get("n"),
            "refs": [r.get("ref") for r in manifest.get("refs") or [] if isinstance(r, dict)],
        },
        "assetUrl": asset_url,
        "readerUrl": reader_url,
        "firstRef": first_ref,
        "message": f"{message} {PUBLISH_NOTE} To save a new version later, pass asset_id {new_id}.",
    }
