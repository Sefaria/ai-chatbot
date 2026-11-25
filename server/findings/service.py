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
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q, QuerySet
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
    # Postgres text cannot hold NUL, and nothing legitimate contains it.
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        raise FindingError(f"`{name}` is required")
    if len(text) > limit:
        raise FindingError(f"`{name}` is at most {limit} characters")
    return text


# Reasons sync hides a finding on its own; saving the finding again clears them. A reason
# staff give with hide_finding is never cleared this way.
AUTO_HIDDEN = ("Source sheet deleted", "Source sheet changed owner")


def _rows(roles: list[dict], sheet: SheetRefs) -> tuple[list[tuple], list[dict]]:
    """The segment rows for a sheet, and the cited sources the sheet actually holds.

    Rows are capped at SEGMENTS_MAX, anchors and positions first, so a huge sheet loses
    support rows rather than the passages it is shown beside.
    """
    rows = refs.rows_for(roles, sheet.expanded, sheet.included)
    rows.sort(key=lambda row: refs.ROLE_RANK[row[3]])
    rows = rows[:SEGMENTS_MAX]
    held = {row[0] for row in rows}
    return rows, [r for r in roles if r["ref"] in held]


def _write_rows(finding: Finding, rows: list[tuple]) -> None:
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


@dataclass(frozen=True)
class Checked:
    skill: skills_catalog.CatalogSkill
    title: str
    conclusion: str
    roles: list[dict]
    sheet_id: int | None


def check(user_id: int, payload: dict, *, need_sheet: bool = True) -> Checked:
    """Everything about a save that can be refused without reading the sheet.

    The MCP tool asks for this (``dry_run``) before it creates a sheet, so a refused save
    does not leave an orphan sheet in the user's account.
    """
    if not isinstance(payload, dict):
        raise FindingError("Expected a JSON object")
    skill = catalog().skill(str(payload.get("skill") or ""))
    if skill is None:
        raise FindingError("Unknown skill, or a skill that is not authorized", status=403)
    title = _text(payload.get("title"), "title", TITLE_MAX)
    conclusion = _text(payload.get("conclusion"), "conclusion", CONCLUSION_MAX)
    roles = _clean_roles(payload.get("roles"))
    sheet_id = None
    if need_sheet or payload.get("sheet_id") is not None:
        try:
            sheet_id = int(payload.get("sheet_id"))
        except (TypeError, ValueError):
            raise FindingError(
                "`sheet_id` must be the number of the finding's source sheet"
            ) from None
    existing = Finding.objects.filter(sheet_id=sheet_id).first() if sheet_id else None
    if existing and existing.sefaria_user_id != user_id:
        raise FindingError("That sheet already holds another user's finding", status=409)
    if not existing:
        _check_daily_limit(user_id)
    return Checked(skill, title, conclusion, roles, sheet_id)


def _check_daily_limit(user_id: int) -> None:
    since = timezone.now() - timedelta(days=1)
    recent = Finding.objects.filter(sefaria_user_id=user_id, created__gte=since).count()
    if recent >= settings.FINDINGS_DAILY_LIMIT:
        raise FindingError("Daily limit of saved findings reached; try again tomorrow", 429)


def record(user_id: int, payload: dict, sheet_reader=fetch_sheet) -> tuple[Finding, bool]:
    """Save a finding for ``user_id`` from the MCP process's request. Returns ``(finding, created)``."""
    checked = check(user_id, payload)
    try:
        sheet = sheet_reader(checked.sheet_id, user_id)
    except Exception as exc:
        logger.warning("Could not read sheet %s for a finding: %s", checked.sheet_id, exc)
        raise FindingError(
            f"Could not read source sheet {checked.sheet_id} from Sefaria", 502
        ) from exc
    if sheet.owner != user_id:
        raise FindingError("A finding's sheet must be one of your own sheets", status=403)
    rows, roles = _rows(checked.roles, sheet)
    if not any(r["role"] == "anchor" for r in roles):
        raise FindingError("None of the anchor sources is on the sheet")

    with transaction.atomic():
        # Locked, so two saves for one sheet at once update one finding in turn.
        finding = Finding.objects.select_for_update().filter(sheet_id=checked.sheet_id).first()
        created = finding is None
        if finding and finding.sefaria_user_id != user_id:
            raise FindingError("That sheet already holds another user's finding", status=409)
        if created:
            _check_daily_limit(user_id)
            finding = Finding(sheet_id=checked.sheet_id, sefaria_user_id=user_id)
        elif finding.hidden and finding.hidden_reason in AUTO_HIDDEN:
            finding.hidden, finding.hidden_reason = False, ""
        finding.skill = checked.skill.name
        finding.skill_version = checked.skill.version
        finding.method = checked.skill.method
        finding.title = checked.title
        finding.conclusion = checked.conclusion
        finding.roles = roles
        finding.sheet_title = sheet.title[:255]
        finding.sheet_modified = sheet.modified
        finding.synced = timezone.now()
        try:
            with transaction.atomic():
                finding.save()
        except IntegrityError:
            # Another request created the finding for this sheet between our read and save.
            raise FindingError("This sheet's finding is being saved; try again", 409) from None
        _write_rows(finding, rows)
    return finding, created


def sync(finding: Finding, sheet_reader=fetch_sheet) -> str:
    """Bring a finding's refs in line with its sheet. Returns what happened.

    A sheet edited since the last read has its refs re-read. A sheet Sefaria says does not
    exist (or a 404) hides the finding. Any other failure leaves it as it is, to be tried
    again next time: a bad night at Sefaria must not hide every finding.
    """
    import httpx

    from chat.V2.agent.sefaria_client import SheetNotFound

    try:
        sheet = sheet_reader(finding.sheet_id, finding.sefaria_user_id)
    except (SheetNotFound, httpx.HTTPStatusError) as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code != 404:
            return "error"
        return _hide(finding, "Source sheet deleted")
    except Exception as exc:
        logger.warning("Sync of finding %s failed: %s", finding.id, exc)
        return "error"
    if sheet.owner != finding.sefaria_user_id:
        return _hide(finding, "Source sheet changed owner")
    if sheet.modified == finding.sheet_modified:
        Finding.objects.filter(pk=finding.pk).update(synced=timezone.now())
        return "unchanged"
    rows, roles = _rows(finding.roles, sheet)
    with transaction.atomic():
        finding.roles = roles
        finding.sheet_title = sheet.title[:255]
        finding.sheet_modified = sheet.modified
        finding.synced = timezone.now()
        finding.save()
        _write_rows(finding, rows)
    return "updated"


def _hide(finding: Finding, reason: str) -> str:
    finding.hidden, finding.hidden_reason = True, reason
    finding.save(update_fields=["hidden", "hidden_reason", "updated"])
    return "hidden"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def sidebar_refs() -> Prefetch:
    """Prefetch only the rows a response shows: a finding can have thousands of support rows."""
    return Prefetch("refs", queryset=FindingRef.objects.filter(role__in=refs.SIDEBAR_ROLES))


def _ref_filter(ref: str) -> Q:
    """Rows in ``ref``: a section, a segment, or a range of segments within one section."""
    segments = refs.segments_in(ref)
    if segments:
        return Q(segment_ref__in=segments)
    return Q(section_ref=ref) | Q(segment_ref=ref)


def matching_ref(qs: QuerySet[Finding], ref: str, all_roles: bool = False) -> QuerySet[Finding]:
    """Findings touching ``ref``, by default as anchor or position."""
    rows = FindingRef.objects.filter(_ref_filter(ref))
    if not all_roles:
        rows = rows.filter(role__in=refs.SIDEBAR_ROLES)
    return qs.filter(id__in=rows.values("finding_id"))


def anchor_refs(finding: Finding, ref: str | None = None) -> list[str]:
    """The anchor and position segments of a finding, within ``ref`` if given."""
    # Filtered in Python so a list of findings prefetched with their refs costs no queries.
    within = set(refs.segments_in(ref)) if ref else set()
    segments = {
        r.segment_ref
        for r in finding.refs.all()
        if r.role in refs.SIDEBAR_ROLES
        and (not ref or ref in (r.section_ref, r.segment_ref) or r.segment_ref in within)
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
