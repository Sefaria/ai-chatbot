"""Deterministic validation for links in final assistant responses."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urlsplit

from .sefaria_client import SefariaClient

SEFARIA_HOST_RE = re.compile(r"(^|\.)sefaria\.org(\.il)?$", re.IGNORECASE)
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
NON_TEXT_SEFARIA_PATH_PREFIXES = {
    "about",
    "account",
    "api",
    "calendars",
    "collections",
    "community",
    "donate",
    "groups",
    "login",
    "people",
    "person",
    "profile",
    "questions",
    "register",
    "sheets",
    "static",
    "texts",
    "topics",
    "visualizations",
}


@dataclass(frozen=True)
class LinkValidationIssue:
    href: str
    reason: str


@dataclass(frozen=True)
class LinkValidationResult:
    issues: list[LinkValidationIssue]

    @property
    def is_valid(self) -> bool:
        return not self.issues


class _AnchorHrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.hrefs.append(value)


def extract_hrefs(response_text: str) -> list[str]:
    """Extract hrefs from HTML anchors and markdown links, preserving order."""
    parser = _AnchorHrefParser()
    parser.feed(response_text or "")
    hrefs = parser.hrefs[:]
    hrefs.extend(match.group(1) for match in MARKDOWN_LINK_RE.finditer(response_text or ""))

    seen: set[str] = set()
    unique: list[str] = []
    for href in hrefs:
        href = href.strip()
        if href and href not in seen:
            seen.add(href)
            unique.append(href)
    return unique


def is_sefaria_hostname(hostname: str) -> bool:
    return bool(SEFARIA_HOST_RE.search(hostname.strip().lower()))


def sefaria_text_ref_from_href(href: str) -> tuple[str | None, str | None]:
    """Return (tref, issue). issue is non-None when the href should be rejected."""
    parsed = urlsplit(href)

    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return None, f"Unsupported URL scheme: {parsed.scheme}"

    if parsed.netloc and not is_sefaria_hostname(parsed.hostname or ""):
        return None, "External, non-Sefaria URL"

    if not parsed.netloc and parsed.path and not parsed.path.startswith("/"):
        # Bare relative paths are ambiguous in the hosted widget. Let the model
        # use absolute Sefaria URLs or root-relative Sefaria paths.
        return None, "Relative link is not an absolute or root-relative Sefaria URL"

    path = unquote(parsed.path or "").lstrip("/")
    if not path:
        return None, None

    first_segment = path.split("/", 1)[0]
    if first_segment in NON_TEXT_SEFARIA_PATH_PREFIXES:
        return None, None

    return path, None


class ResponseLinkValidator:
    """Validates response links before a final response is shown to users."""

    def __init__(self, client: SefariaClient):
        self.client = client

    async def validate_response(self, response_text: str) -> LinkValidationResult:
        issues: list[LinkValidationIssue] = []
        for href in extract_hrefs(response_text):
            tref, issue = sefaria_text_ref_from_href(href)
            if issue:
                issues.append(LinkValidationIssue(href=href, reason=issue))
                continue
            if not tref:
                continue
            try:
                ref_data = await self.client.strict_resolve_ref(tref)
            except Exception as exc:
                issues.append(
                    LinkValidationIssue(
                        href=href,
                        reason=f"Could not validate Sefaria text ref: {exc}",
                    )
                )
                continue
            if not ref_data:
                issues.append(LinkValidationIssue(href=href, reason="Invalid Sefaria text ref"))
        return LinkValidationResult(issues=issues)


def format_link_validation_issues(issues: list[LinkValidationIssue]) -> str:
    return "\n".join(f"- {issue.href}: {issue.reason}" for issue in issues)


def normalize_validation_result(item: str, ref_data: dict[str, Any] | None) -> dict[str, Any]:
    if not ref_data:
        return {"input": item, "is_valid": False}
    return {
        "input": item,
        "is_valid": True,
        "normalized": ref_data.get("en") or ref_data.get("normalized", ""),
        "hebrew": ref_data.get("he") or ref_data.get("hebrew", ""),
        "url_ref": ref_data.get("url_ref", ""),
    }
