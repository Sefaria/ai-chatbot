"""Recording, refreshing and reading findings.

Trust works like this. Who saved a finding comes from a write token only our MCP process can
mint. Which passages it touches comes from Sefaria, read from the user's own sheet, never from
the request. Whether it is shown depends on the catalog: only findings of skills that are
authorized now, and not hidden by staff, are public.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django.utils import timezone

import skills_catalog
from chat.V2.agent.sefaria_client import SefariaClient
from mcp_auth import sefaria_tokens

from . import refs
from .models import Finding, FindingRef

logger = logging.getLogger(__name__)

TITLE_MAX = 160
CONCLUSION_MAX = 4000
SOURCES_MAX = 30
SEGMENTS_MAX = 2000


class FindingError(Exception):
    """A request we refuse, with the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SheetRefs:
    id: int
    owner: int | None
    title: str
    modified: str
    expanded: list[str]
    included: list[str] | None = None


def catalog() -> skills_catalog.Catalog:
    return skills_catalog.load()


def visible() -> QuerySet[Finding]:
    """Findings the public can see: not hidden, and their skill is authorized now."""
    return Finding.objects.filter(hidden=False, skill__in=catalog().served_names())


# ---------------------------------------------------------------------------
# Reading a sheet from Sefaria
# ---------------------------------------------------------------------------


def fetch_sheet(sheet_id: int, as_user: int | None) -> SheetRefs:
    """Read a sheet's refs from Sefaria, as ``as_user`` when the MCP token secret is set."""

    async def read():
        client = SefariaClient()
        if as_user and settings.MCP_TOKEN_SECRET:
            client.set_user_session(
                str(as_user), sefaria_tokens.mint(int(as_user), settings.MCP_TOKEN_SECRET)
            )
        try:
            return await client.get_source_sheet_refs(sheet_id)
        finally:
            await client.close()

    data = asyncio.run(read())
    owner = data.get("owner")
    return SheetRefs(
        id=int(data["id"]),
        owner=int(owner) if owner is not None else None,
        title=data["title"],
        modified=data["dateModified"],
        expanded=data["expandedRefs"] or data["includedRefs"],
        included=data["includedRefs"],
    )


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _clean_roles(raw) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise FindingError("`roles` must list the sources the finding relies on")
    if len(raw) > SOURCES_MAX:
        raise FindingError(f"A finding cites at most {SOURCES_MAX} sources")
    roles, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise FindingError("Each role must be an object with `ref` and `role`")
        ref, role = str(item.get("ref") or "").strip(), item.get("role")
        if not ref or role not in refs.ROLE_RANK:
            raise FindingError(
                "Each role needs a `ref` and a `role` of anchor, position or support"
            )
        if ref not in seen:
            seen.add(ref)
            roles.append({"ref": ref, "role": role})
    if not any(r["role"] == "anchor" for r in roles):
        raise FindingError("A finding needs at least one `anchor` source: the passage it studies")
    return roles


def _text(value, name: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise FindingError(f"`{name}` is required")
    if len(text) > limit:
        raise FindingError(f"`{name}` is at most {limit} characters")
    return text


def _write_rows(finding: Finding, sheet: SheetRefs) -> None:
    rows = refs.rows_for(finding.roles, sheet.expanded, sheet.included)[:SEGMENTS_MAX]
    finding.refs.all().delete()
    FindingRef.objects.bulk_create(
        FindingRef(
            finding=finding,
            source_ref=source[:255],
            segment_ref=segment[:255],
            section_ref=section[:255],
            book=refs.book_of(segment)[:200],
            role=role,
        )
        for source, segment, section, role in rows
    )


def record(user_id: int, payload: dict, sheet_reader=fetch_sheet) -> tuple[Finding, bool]:
    """Save a finding for ``user_id`` from the MCP process's request. Returns ``(finding, created)``."""
    if not isinstance(payload, dict):
        raise FindingError("Expected a JSON object")
    skill = catalog().skill(str(payload.get("skill") or ""))
    if skill is None:
        raise FindingError("Unknown skill, or a skill that is not authorized", status=403)
    title = _text(payload.get("title"), "title", TITLE_MAX)
    conclusion = _text(payload.get("conclusion"), "conclusion", CONCLUSION_MAX)
    roles = _clean_roles(payload.get("roles"))
    try:
        sheet_id = int(payload.get("sheet_id"))
    except (TypeError, ValueError):
        raise FindingError("`sheet_id` must be the number of the finding's source sheet") from None

    existing = Finding.objects.filter(sheet_id=sheet_id).first()
    if existing and existing.sefaria_user_id != user_id:
        raise FindingError("That sheet already holds another user's finding", status=409)
    if not existing:
        since = timezone.now() - timedelta(days=1)
        recent = Finding.objects.filter(sefaria_user_id=user_id, created__gte=since).count()
        if recent >= settings.FINDINGS_DAILY_LIMIT:
            raise FindingError("Daily limit of saved findings reached; try again tomorrow", 429)

    try:
        sheet = sheet_reader(sheet_id, user_id)
    except Exception as exc:
        logger.warning("Could not read sheet %s for a finding: %s", sheet_id, exc)
        raise FindingError(f"Could not read source sheet {sheet_id} from Sefaria", 502) from exc
    if sheet.owner != user_id:
        raise FindingError("A finding's sheet must be one of your own sheets", status=403)

    with transaction.atomic():
        finding = existing or Finding(sheet_id=sheet_id, sefaria_user_id=user_id)
        finding.skill = skill.name
        finding.skill_version = skill.version
        finding.method = skill.method
        finding.title = title
        finding.conclusion = conclusion
        finding.roles = roles
        finding.sheet_title = sheet.title[:255]
        finding.sheet_modified = sheet.modified
        finding.synced = timezone.now()
        finding.save()
        _write_rows(finding, sheet)
    return finding, existing is None


def sync(finding: Finding, sheet_reader=fetch_sheet) -> str:
    """Bring a finding's refs in line with its sheet. Returns what happened.

    A sheet edited since the last read has its refs re-read; a deleted sheet hides the
    finding. Other failures leave it as it is, to be tried again next time.
    """
    import httpx

    try:
        sheet = sheet_reader(finding.sheet_id, finding.sefaria_user_id)
    except (ValueError, httpx.HTTPStatusError) as exc:
        gone = isinstance(exc, ValueError) or exc.response.status_code == 404
        if not gone:
            return "error"
        finding.hidden = True
        finding.hidden_reason = "Source sheet deleted"
        finding.save(update_fields=["hidden", "hidden_reason", "updated"])
        return "hidden"
    except Exception as exc:
        logger.warning("Sync of finding %s failed: %s", finding.id, exc)
        return "error"
    if sheet.owner != finding.sefaria_user_id:
        finding.hidden = True
        finding.hidden_reason = "Source sheet changed owner"
        finding.save(update_fields=["hidden", "hidden_reason", "updated"])
        return "hidden"
    if sheet.modified == finding.sheet_modified:
        Finding.objects.filter(pk=finding.pk).update(synced=timezone.now())
        return "unchanged"
    with transaction.atomic():
        finding.sheet_title = sheet.title[:255]
        finding.sheet_modified = sheet.modified
        finding.synced = timezone.now()
        finding.save()
        _write_rows(finding, sheet)
    return "updated"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def matching_ref(qs: QuerySet[Finding], ref: str, all_roles: bool = False) -> QuerySet[Finding]:
    """Findings touching ``ref``, a section or a segment, by default as anchor or position."""
    rows = FindingRef.objects.filter(Q(section_ref=ref) | Q(segment_ref=ref))
    if not all_roles:
        rows = rows.filter(role__in=refs.SIDEBAR_ROLES)
    return qs.filter(id__in=rows.values("finding_id"))


def anchor_refs(finding: Finding, ref: str | None = None) -> list[str]:
    """The anchor and position segments of a finding, within ``ref`` if given."""
    # Filtered in Python so a list of findings prefetched with their refs costs no queries.
    segments = {
        r.segment_ref
        for r in finding.refs.all()
        if r.role in refs.SIDEBAR_ROLES and (not ref or ref in (r.section_ref, r.segment_ref))
    }
    return sorted(segments, key=refs.sort_key)


def reader_url(ref: str) -> str:
    return f"{settings.SEFARIA_WEB_URL}/{refs.to_url(ref)}"


def finding_url(finding: Finding) -> str:
    return f"{settings.CHAT_PUBLIC_URL}/findings/{finding.id}/"


def to_json(finding: Finding, ref: str | None = None) -> dict:
    cat = catalog()
    method = cat.methods.get(finding.method)
    skill = cat.skills.get(finding.skill)
    return {
        "id": str(finding.id),
        "url": finding_url(finding),
        "method": {
            "slug": finding.method,
            "title": {
                "en": method.title_en if method else finding.method,
                "he": method.title_he if method else "",
            },
        },
        "skill": {
            "name": finding.skill,
            "title": skill.title if skill else finding.skill,
            "version": finding.skill_version,
        },
        "title": finding.title,
        "conclusion": finding.conclusion,
        "label": method.label if method else "AI-assisted reading",
        "anchorRefs": anchor_refs(finding, ref),
        "refs": [{**r, "url": reader_url(r["ref"])} for r in finding.roles],
        "sheetUrl": f"{settings.SEFARIA_WEB_URL}/sheets/{finding.sheet_id}",
        "created": finding.created.isoformat(),
    }


def method_counts() -> dict[str, int]:
    return dict(visible().values_list("method").annotate(n=Count("id")).order_by())


def skill_counts() -> dict[str, int]:
    return dict(visible().values_list("skill").annotate(n=Count("id")).order_by())


def books_of(qs: QuerySet[Finding]) -> list[tuple[str, int]]:
    """Books a set of findings is anchored in, with how many findings each, most first."""
    rows = (
        FindingRef.objects.filter(finding__in=qs, role__in=refs.SIDEBAR_ROLES)
        .values("book")
        .annotate(n=Count("finding", distinct=True))
        .order_by("-n", "book")
    )
    return [(r["book"], r["n"]) for r in rows]


def search(
    qs: QuerySet[Finding] | None = None,
    *,
    ref: str | None = None,
    method: str | None = None,
    skill: str | None = None,
    book: str | None = None,
    text: str | None = None,
) -> QuerySet[Finding]:
    qs = visible() if qs is None else qs
    if ref:
        qs = matching_ref(qs, refs.from_url(ref))
    if method:
        qs = qs.filter(method=method)
    if skill:
        qs = qs.filter(skill=skill)
    if book:
        qs = qs.filter(
            id__in=FindingRef.objects.filter(book=book, role__in=refs.SIDEBAR_ROLES).values(
                "finding_id"
            )
        )
    if text:
        qs = qs.filter(Q(title__icontains=text) | Q(conclusion__icontains=text))
    return qs
