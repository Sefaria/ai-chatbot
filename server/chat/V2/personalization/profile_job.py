"""The Profile job: read new prompts, gate, extract, write, check, store.

One function, `run_profile_job`, serves every trigger. Rules kept in code:

- one job at a time per user (the lock); a busy lock skips the run, the next trigger picks it up
- the rule-based sensitive screen runs before anything is sent anywhere
- the watermark is the timestamp of the newest prompt read; it moves only when a run ends without
  needing those prompts again (applied, or nothing to say), never on a failure or a rejection
- model and Jev calls happen outside any database transaction
- a failed code check leaves the Profile and the watermark alone; the third failure for one
  watermark parks the user until an explicit `run_now`
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from chat.models import PersonalizationSettings, ProfileWriterRun
from chat.V2.prompts.chapters import chapter_sha, load_chapter

from . import jev_questions as q
from . import quota
from .checks import MAX_DYNAMIC, MAX_WORDS, check_profile, normalize_heading
from .core_slots import CORE_HEADINGS, CORE_KEYS
from .jev import JevClient
from .jev_gate import ask_gate
from .llm import AnthropicLLMClient, LLMClient
from .parsing import clean_text, parse_json_object
from .profile_store import (
    PromptRow,
    SectionData,
    get_sections,
    get_settings,
    record_run,
    render_profile,
    replace_sections,
    unprocessed_user_prompts,
)
from .signals import is_sensitive, session_language

logger = logging.getLogger(__name__)

EXTRACTION_CHAPTER = "profile_extraction"
WRITER_CHAPTER = "profile_writer"
STALE_DAYS = 60
PARK_AFTER = 3
MAX_PROMPT_CHARS = q.MAX_PROMPT_CHARS
ACTIONS = ("add", "update", "confirm", "none")
ORIGINS = ("stated", "inferred")
REVISION_MOVED = "revision_moved"

WRITER_SUFFIX = """

## Output for this job
You are given only the sections to write, each under a "## Heading" line, with its current body
and the notes to use. Return only JSON, no prose:
{"sections": [{"heading": "<the heading, unchanged>", "body": "<the new body>"}]}
One entry per listed heading. A body is plain second-person prose. Write an empty body when the
notes do not support anything. Keep all bodies together within the word budget given."""


@dataclass
class RunResult:
    status: str  # applied | no_change | rejected | failed | busy | quota
    reason: str | None = None
    run_id: int | None = None


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""

    def add(self, result, model: str) -> None:
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        self.cost_usd += result.cost_usd
        self.model = model


@dataclass
class _Ctx:
    user_id: str
    trigger: str
    window: str
    started_at: datetime
    since: datetime | None
    base_revision: int
    prompts: list[PromptRow] = field(default_factory=list)
    extraction: dict = field(default_factory=dict)
    profile_before: str = ""
    usage: _Usage = field(default_factory=_Usage)
    watermark_to: datetime | None = None


class _Fail(Exception):
    def __init__(self, status: str, reason: str, failed_check: str = ""):
        super().__init__(reason)
        self.status, self.reason, self.failed_check = status, reason, failed_check


def run_profile_job(
    user_id: str,
    trigger: str,
    *,
    window: str = "session",
    now: datetime | None = None,
    llm: LLMClient | None = None,
    jev: JevClient | None = None,
) -> RunResult:
    if window != "session":
        raise NotImplementedError("the dream window is not built yet")
    now = now or timezone.now()
    if not quota.acquire_job_lock(user_id, now):
        return RunResult("busy", "job_running")
    try:
        return _run(user_id, trigger, window, now, llm or AnthropicLLMClient(), jev)
    finally:
        quota.release_job_lock(user_id)


def _run(user_id, trigger, window, now, llm, jev) -> RunResult:
    current = get_settings(user_id)
    ctx = _Ctx(user_id, trigger, window, now, current.processed_through, current.profile_revision)
    if trigger != "run_now" and _is_parked(user_id, window, ctx.since):
        return RunResult("failed", "parked")
    try:
        return _steps(ctx, now, llm, jev)
    except _Fail as fail:
        if fail.status == "quota":
            return RunResult("quota", fail.reason)
        return _finish(ctx, fail.status, fail.reason, fail.failed_check)
    except Exception as exc:  # a model or store error: the run fails, the Profile stays
        logger.warning("profile job failed: %s", type(exc).__name__)
        return _finish(ctx, "failed", f"error:{type(exc).__name__}")


def _is_parked(user_id: str, window: str, since: datetime | None) -> bool:
    """The last PARK_AFTER runs all failed or were rejected for this same watermark."""
    recent = list(
        ProfileWriterRun.objects.filter(user_id=user_id, window=window)
        .exclude(reason=REVISION_MOVED)
        .order_by("-id")[:PARK_AFTER]
    )
    return len(recent) == PARK_AFTER and all(
        r.status in ("failed", "rejected") and r.watermark_from == since for r in recent
    )


def _steps(ctx: _Ctx, now: datetime, llm: LLMClient, jev: JevClient | None) -> RunResult:
    prompts = unprocessed_user_prompts(ctx.user_id, ctx.since)[: q.MAX_PROMPTS_PER_RUN]
    if not prompts:
        return _finish(ctx, "no_change", "no_new_prompts")
    ctx.prompts = prompts
    ctx.watermark_to = max(p.server_timestamp for p in prompts)

    kept = [p for p in prompts if not is_sensitive(p.text)]
    if not kept:
        return _done_nothing(ctx, "sensitive_only")

    _take_quota(ctx.user_id, now)
    texts = [clean_text(p.text)[:MAX_PROMPT_CHARS] for p in kept]
    gate = ask_gate(texts, settings.PERSONALIZATION_JEV_GATE, jev)
    ctx.extraction["gate"] = {"source": gate.source, "scores": gate.scores}
    kept = [p for i, p in enumerate(kept) if i not in gate.sensitive]
    texts = [t for i, t in enumerate(texts) if i not in gate.sensitive]
    if not kept:
        return _done_nothing(ctx, "sensitive_only")
    if not gate.relevant:
        return _done_nothing(ctx, "not_relevant")

    sections = get_sections(ctx.user_id)
    ctx.profile_before = render_profile(sections)
    instructions = clean_text(get_settings(ctx.user_id).instructions_text)
    proposals = _extract(ctx, llm, sections, kept, texts, gate.slots, instructions)
    newest = max(p.server_timestamp for p in kept)
    new_sections, touched = _apply_proposals(sections, proposals, gate.slots, newest)
    bodies = _write(ctx, llm, touched, new_sections, instructions)
    final, wrote = _merge(new_sections, touched, bodies, now)
    if final == sections:
        return _done_nothing(ctx, "nothing_to_write")

    result = check_profile(final, llm)  # a model call: outside any transaction
    if not result.ok:
        raise _Fail("rejected", result.failed or "check", result.failed or "")
    if not replace_sections(
        ctx.user_id,
        ctx.base_revision,
        final,
        updated_by="la",
        processed_through=ctx.watermark_to,
        set_notice=wrote,
    ):
        raise _Fail("failed", REVISION_MOVED)
    return _finish(ctx, "applied", None, profile_after=render_profile(final))


def _take_quota(user_id: str, now: datetime) -> None:
    hour = now.replace(minute=0, second=0, microsecond=0)
    day = hour.replace(hour=0)
    allowed = quota.take_quotas(
        "job",
        [
            (
                user_id,
                hour,
                quota.limit("PERSONALIZATION_JOB_PER_HOUR", quota.JOB_PER_HOUR_DEFAULT),
            ),
            (
                quota.ALL_KEY,
                day,
                quota.limit("PERSONALIZATION_JOB_PER_DAY", quota.JOB_PER_DAY_DEFAULT),
            ),
        ],
    )
    if not allowed:
        raise _Fail("quota", "over_limit")


def _advance_watermark(ctx: _Ctx) -> None:
    """Move the watermark forward only; never past a Clear or an edit that moved the revision."""
    if ctx.watermark_to is None:
        return
    PersonalizationSettings.objects.get_or_create(user_id=ctx.user_id)
    with transaction.atomic():
        row = (
            PersonalizationSettings.objects.select_for_update()
            .filter(user_id=ctx.user_id, profile_revision=ctx.base_revision)
            .first()
        )
        if row and (row.processed_through is None or row.processed_through < ctx.watermark_to):
            row.processed_through = ctx.watermark_to
            row.save(update_fields=["processed_through"])


def _done_nothing(ctx: _Ctx, reason: str) -> RunResult:
    _advance_watermark(ctx)
    return _finish(ctx, "no_change", reason)


def _call(ctx: _Ctx, llm: LLMClient, system: str, user: str, max_tokens: int) -> str:
    model = settings.PERSONALIZATION_MODEL
    result = llm.complete(
        system=system,
        user=user,
        model=model,
        max_tokens=max_tokens,
        timeout=settings.PERSONALIZATION_MODEL_TIMEOUT,
    )
    ctx.usage.add(result, model)
    return result.text


def _extract(ctx, llm, sections, prompts, texts, slots, instructions) -> list[dict]:
    session_ids = {}
    lines = []
    for p, text in zip(prompts, texts, strict=True):
        label = session_ids.setdefault(p.session_id, f"S{len(session_ids) + 1}")
        lines.append(f"[{label}] {text}")
    language = session_language(texts) or "unknown"
    headings = "\n".join(f"- {s.heading}" for s in sections) or "(none)"
    allowed = ", ".join(k for k in CORE_KEYS if k in slots) or "(none)"
    user = (
        f"<headings>\n{headings}\n</headings>\n"
        f"<allowed_core_slots>{allowed}</allowed_core_slots>\n"
        f"<instructions>{instructions or '(none)'}</instructions>\n"
        f"<language_signal>{language}</language_signal>\n"
        f"<prompts>\n" + "\n".join(lines) + "\n</prompts>"
    )
    raw = _call(ctx, llm, load_chapter(EXTRACTION_CHAPTER), user, 1200)
    parsed = parse_json_object(raw)
    if not parsed or not isinstance(parsed.get("sections"), list):
        raise _Fail("failed", "bad_extraction")
    proposals = []
    for entry in parsed["sections"]:
        if not isinstance(entry, dict) or entry.get("action") not in ACTIONS:
            continue
        key = entry.get("key")
        sessions = entry.get("sessions")
        proposals.append(
            {
                "key": key if isinstance(key, str) else None,
                "heading": clean_text(str(entry.get("heading") or ""))[:100],
                "action": entry["action"],
                "origin": entry.get("origin") if entry.get("origin") in ORIGINS else "inferred",
                "notes": [clean_text(str(n))[:300] for n in (entry.get("notes") or [])][:6]
                if isinstance(entry.get("notes"), list)
                else [],
                "sessions": min(sessions, len(session_ids))
                if isinstance(sessions, int) and not isinstance(sessions, bool)
                else 0,
            }
        )
    ctx.extraction["extraction"] = proposals
    ctx.extraction["sessions_in_run"] = len(session_ids)
    return proposals


def _apply_proposals(sections, proposals, slots, newest):
    """Turn proposals into: the section list (confirms applied) and what the writer must write.

    `touched` maps a heading to the section (None when new), its key, kind, origin, notes and the
    time of its newest evidence.
    """
    current = [SectionData(**vars(s)) for s in sections]
    by_heading = {normalize_heading(s.heading): s for s in current}
    dynamic_count = sum(1 for s in current if s.kind == "dynamic")
    touched: dict[str, dict] = {}
    for p in proposals:
        action = p["action"]
        if action == "none":
            continue
        if p["key"] is not None:
            if p["key"] not in CORE_KEYS or p["key"] not in slots:
                continue
            heading, kind = CORE_HEADINGS[p["key"]], "core"
        else:
            heading, kind = p["heading"], "dynamic"
            if not heading or is_sensitive(heading):
                continue
        existing = by_heading.get(normalize_heading(heading))
        if existing is not None and existing.kind == "core" and p["key"] is None:
            continue  # a core section changes only through its slot key
        if kind == "dynamic" and existing is None:
            core_headings = {normalize_heading(h) for h in CORE_HEADINGS.values()}
            opened = sum(1 for t in touched.values() if t["section"] is None)
            if (
                action in ("add", "update")
                and normalize_heading(heading) not in core_headings
                and p["sessions"] >= 2
                and dynamic_count + opened < MAX_DYNAMIC
            ):
                touched[heading] = _touch(None, p, newest, heading, kind)
            continue
        if existing is None:
            if action in ("add", "update"):
                touched[heading] = _touch(None, p, newest, heading, kind)
            continue
        if action == "confirm":
            existing.last_supported_at = max(existing.last_supported_at, newest)
            continue
        if existing.origin == "stated" and p["origin"] == "inferred":
            continue
        touched[heading] = _touch(existing, p, newest, heading, kind)
    return current, touched


def _touch(existing, p, newest, heading, kind) -> dict:
    return {
        "section": existing,
        "supported": newest,
        "key": p["key"],
        "kind": kind,
        "origin": p["origin"],
        "notes": p["notes"],
    }


def _write(ctx, llm, touched: dict, sections, instructions) -> dict[str, str]:
    if not touched:
        return {}
    untouched_words = sum(len(s.body.split()) for s in sections if s.heading not in touched)
    budget = max(MAX_WORDS - untouched_words, 0)
    blocks = []
    for heading, t in touched.items():
        body = t["section"].body if t["section"] else "(new section)"
        notes = "\n".join(f"- {n}" for n in t["notes"]) or "- (none)"
        blocks.append(f"## {heading}\nCurrent: {body}\nNotes:\n{notes}")
    user = (
        f"<instructions>{instructions or '(none)'}</instructions>\n"
        f"<word_budget>{budget}</word_budget>\n<sections>\n" + "\n\n".join(blocks) + "\n</sections>"
    )
    raw = _call(ctx, llm, load_chapter(WRITER_CHAPTER) + WRITER_SUFFIX, user, 900)
    parsed = parse_json_object(raw)
    if not parsed or not isinstance(parsed.get("sections"), list):
        raise _Fail("failed", "bad_writer_output")
    wanted = {normalize_heading(h): h for h in touched}
    bodies: dict[str, str] = {}
    for entry in parsed["sections"]:
        if isinstance(entry, dict) and isinstance(entry.get("heading"), str):
            heading = wanted.get(normalize_heading(entry["heading"]))
            if heading and isinstance(entry.get("body"), str):
                bodies[heading] = clean_text(entry["body"])
    return bodies


def _merge(sections, touched, bodies, now):
    """The final section list: written bodies in, stale sections out, core first in slot order.
    Returns (sections, whether any body the user reads changed)."""
    wrote = False
    cutoff = now - timedelta(days=STALE_DAYS)
    by_heading = {s.heading: s for s in sections}
    new_rows = []
    for heading, t in touched.items():
        body = bodies.get(heading)
        if body is None:
            continue  # the writer left it out: unchanged
        if not body:
            if t["section"] is not None:
                by_heading.pop(heading, None)
                wrote = True
            continue
        if t["section"] is not None:
            s = t["section"]
            if s.body != body:
                wrote = True
            s.body, s.origin = body, t["origin"]
            s.last_supported_at = max(s.last_supported_at, t["supported"])
        else:
            wrote = True
            new_rows.append(
                SectionData(t["key"], t["kind"], heading, body, t["origin"], t["supported"], 0)
            )
    all_rows = [s for s in by_heading.values() if s.last_supported_at >= cutoff] + new_rows
    core_order = {k: i for i, k in enumerate(CORE_KEYS)}
    core = sorted(
        (s for s in all_rows if s.kind == "core"), key=lambda s: core_order.get(s.key, 99)
    )
    dynamic = [s for s in all_rows if s.kind != "core"]
    ordered = core + dynamic
    for i, s in enumerate(ordered):
        s.position = i
    return ordered, wrote or len(ordered) != len(sections)


def _finish(
    ctx: _Ctx, status: str, reason: str | None, failed_check: str = "", **extra
) -> RunResult:
    finished = timezone.now()
    run_id = record_run(
        user_id=ctx.user_id,
        trigger=ctx.trigger,
        window=ctx.window,
        started_at=ctx.started_at,
        finished_at=finished,
        watermark_from=ctx.since,
        watermark_to=ctx.watermark_to,
        prompt_ids=[p.message_id for p in ctx.prompts],
        extraction=json.dumps(ctx.extraction, ensure_ascii=False, default=str)
        if ctx.extraction
        else "",
        profile_before=ctx.profile_before,
        profile_after=extra.get("profile_after", ""),
        chapter_hashes={
            EXTRACTION_CHAPTER: chapter_sha(EXTRACTION_CHAPTER),
            WRITER_CHAPTER: chapter_sha(WRITER_CHAPTER),
        },
        model=ctx.usage.model,
        input_tokens=ctx.usage.input_tokens or None,
        output_tokens=ctx.usage.output_tokens or None,
        cost_usd=round(ctx.usage.cost_usd, 6) if ctx.usage.input_tokens else None,
        status=status,
        failed_check=failed_check,
        reason=(reason or "")[:200],
    )
    return RunResult(status, reason, run_id)
