"""The Profile job: read new prompts, gate, extract, write, check, store.

One function, `run_profile_job`, serves every trigger. Rules kept in code:

- one job at a time per user (the lock); a busy lock skips the run, the next trigger picks it up
- the rule-based sensitive screen runs before anything is sent anywhere
- the watermark is the timestamp of the newest prompt read; it moves only when a run ends without
  needing those prompts again (applied, or nothing to say), never on a failure or a rejection
- model and Jev calls happen outside any database transaction
- a failed code check leaves the Profile and the watermark alone; the third failure for one
  watermark parks the user until an explicit `run_now`
- a run row holds no free text derived from the user: `extraction` is a counts-and-codes summary
  (`_summary`), `profile_after` is stored only for an applied run (it then equals the stored
  Profile), and reason and failed-check fields are fixed codes
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
    record_run_guarded,
    refresh_support_dates,
    render_profile,
    replace_sections,
    unprocessed_user_prompts,
)
from .signals import is_sensitive, session_language

logger = logging.getLogger(__name__)

EXTRACTION_CHAPTER = "profile_extraction"
WRITER_CHAPTER = "profile_writer"
DREAM_CHAPTER = "profile_dream"
DREAM_MAX_PROMPTS = 200  # the newest of the window
STALE_DAYS = 60
PARK_AFTER = 3
MAX_PROMPT_CHARS = q.MAX_PROMPT_CHARS
ACTIONS = ("add", "update", "confirm", "none")
ORIGINS = ("stated", "inferred")
REVISION_MOVED = "revision_moved"
PROFILE_OFF = "profile_off"
BATCH_SKIPPED = "batch_skipped"

WRITER_SUFFIX = """

## Output for this job
You are given only the sections to write, each under a "## Heading" line, with its current body
and the notes to use. Return only JSON, no prose:
{"sections": [{"heading": "<the heading, unchanged>", "body": "<the new body>"}]}
One entry per listed heading. A body is plain second-person prose. Always write a non-empty body: when the
notes do not support a change, repeat the current body. A new section has no current text (its
Current line is empty): write it from the notes alone. Keep all bodies together within the word
budget given."""
NEW_SECTION_LITERAL = "(new section)"  # the old stand-in for "no current text"; never a valid body


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
    # What the run row may keep about the run: counts and codes, never text (see `_summary`).
    kept_count: int | None = None
    gate: dict | None = None
    sections: list[dict] = field(default_factory=list)
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
    if window not in ("session", "dream"):
        raise ValueError(f"unknown window: {window!r}")
    if not get_settings(user_id).profile_enabled:
        return RunResult("no_change", PROFILE_OFF)  # before any read, model or Jev call; no row
    now = now or timezone.now()
    lease = quota.acquire_job_lock(user_id, now)
    if not lease:
        return RunResult("busy", "job_running")
    try:
        return _run(user_id, trigger, window, now, llm or AnthropicLLMClient(), jev)
    finally:
        quota.release_job_lock(user_id, lease)


def _run(user_id, trigger, window, now, llm, jev) -> RunResult:
    current = get_settings(user_id)
    since = current.processed_through if window == "session" else None
    ctx = _Ctx(user_id, trigger, window, now, since, current.profile_revision)
    if trigger != "run_now":
        stuck = _stuck_run(user_id, window, ctx.since)
        if stuck is not None:
            if not unprocessed_user_prompts(user_id, stuck.started_at):
                return RunResult("failed", "parked")  # parked until a new prompt arrives
            if window == "session" and stuck.watermark_to is not None:
                _quarantine(ctx, stuck)
                ctx.since = get_settings(user_id).processed_through
                ctx.base_revision = get_settings(user_id).profile_revision
                ctx.prompts, ctx.kept_count, ctx.watermark_to = [], None, None
    try:
        if window == "dream":
            return _dream_steps(ctx, now, llm, jev, current.processed_through)
        return _steps(ctx, now, llm, jev)
    except _Fail as fail:
        if fail.status == "quota":
            return RunResult("quota", fail.reason)
        return _finish(ctx, fail.status, fail.reason, fail.failed_check)
    except Exception as exc:  # a model or store error: the run fails, the Profile stays
        logger.warning("profile job failed: %s", type(exc).__name__)
        return _finish(ctx, "failed", f"error:{type(exc).__name__}")


def _stuck_run(user_id: str, window: str, since: datetime | None):
    """The newest run when the last PARK_AFTER runs all failed or were rejected for this same
    watermark, else None."""
    recent = list(
        ProfileWriterRun.objects.filter(user_id=user_id, window=window)
        .exclude(reason=REVISION_MOVED)
        .order_by("-id")[:PARK_AFTER]
    )
    if len(recent) < PARK_AFTER or not all(
        r.status in ("failed", "rejected") and r.watermark_from == since for r in recent
    ):
        return None
    return recent[0]


def is_parked(user_id: str, window: str, since: datetime | None) -> bool:
    """Stuck, and no prompt has arrived since the last failed run (a new prompt gives the next
    trigger a fresh try: the session window quarantines the failing batch, the dream retries)."""
    stuck = _stuck_run(user_id, window, since)
    return stuck is not None and not unprocessed_user_prompts(user_id, stuck.started_at)


def _quarantine(ctx: _Ctx, stuck) -> None:
    """Skip the batch the failing runs kept reading: move the watermark past it without any model
    call, and record one `no_change` row with the prompt ids only. Later prompts then process
    normally. `run_now` never gets here: it stays an explicit retry of the same batch."""
    batch = [
        p
        for p in unprocessed_user_prompts(ctx.user_id, ctx.since)
        if p.server_timestamp <= stuck.watermark_to
    ][: q.MAX_PROMPTS_PER_RUN]
    if not batch:
        return
    ctx.prompts, ctx.kept_count = batch, len(batch)
    ctx.watermark_to = max(p.server_timestamp for p in batch)
    _advance_watermark(ctx)
    _finish(ctx, "no_change", BATCH_SKIPPED)


def _steps(ctx: _Ctx, now: datetime, llm: LLMClient, jev: JevClient | None) -> RunResult:
    prompts = unprocessed_user_prompts(ctx.user_id, ctx.since)[: q.MAX_PROMPTS_PER_RUN]
    if not prompts:
        return _finish(ctx, "no_change", "no_new_prompts")
    ctx.prompts = prompts
    ctx.watermark_to = max(p.server_timestamp for p in prompts)

    kept = [p for p in prompts if not is_sensitive(p.text)]
    ctx.kept_count = len(kept)
    if not kept:
        return _done_nothing(ctx, "sensitive_only")

    _take_quota(ctx.user_id, now)
    texts = [clean_text(p.text)[:MAX_PROMPT_CHARS] for p in kept]
    gate = ask_gate(texts, settings.PERSONALIZATION_JEV_GATE, jev)
    ctx.gate = {"source": gate.source, "scores": gate.scores}
    kept = [p for i, p in enumerate(kept) if i not in gate.sensitive]
    texts = [t for i, t in enumerate(texts) if i not in gate.sensitive]
    ctx.kept_count = len(kept)
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
    ctx.sections = [
        _section_summary(p["key"], p["action"], p["origin"], p["sessions"]) for p in proposals
    ]
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
        body = t["section"].body if t["section"] else ""  # a new section has no current text
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
        heading = (
            wanted.get(normalize_heading(entry["heading"]))
            if isinstance(entry, dict) and isinstance(entry.get("heading"), str)
            else None
        )
        body = clean_text(entry["body"]) if heading and isinstance(entry.get("body"), str) else ""
        if not heading or not body or _is_placeholder(body):
            # unknown heading, or a body that is missing, empty or the old placeholder
            raise _Fail("failed", "bad_writer_output")
        bodies[heading] = body
    if set(bodies) != set(touched):  # the writer left a requested section out
        raise _Fail("failed", "bad_writer_output")
    return bodies


def _is_placeholder(body: str) -> bool:
    return body.strip().strip(".").strip().lower() == NEW_SECTION_LITERAL


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


# ---- the dream: rebuild the whole Profile from the last 60 days -------------------------------


@dataclass
class _Candidate:
    """What the model proposed for one section, before the code rules."""

    key: str | None
    heading: str
    body: str
    origin: str
    support: list[int]


def _dream_prompts(ctx: _Ctx, now: datetime, jev: JevClient | None):
    """The window's screened prompts, oldest first (the newest DREAM_MAX_PROMPTS of them)."""
    rows = unprocessed_user_prompts(ctx.user_id, now - timedelta(days=STALE_DAYS))
    rows = rows[-DREAM_MAX_PROMPTS:]
    if rows:
        ctx.watermark_to = max(p.server_timestamp for p in rows)
    ctx.prompts = rows
    kept = [p for p in rows if not is_sensitive(p.text)]
    texts = [clean_text(p.text)[:MAX_PROMPT_CHARS] for p in kept]
    dropped: set[int] = set()
    step = q.MAX_PROMPTS_PER_RUN
    for start in range(0, len(texts), step):  # Jev's sensitive screen, a chunk at a time
        gate = ask_gate(texts[start : start + step], settings.PERSONALIZATION_JEV_GATE, jev)
        dropped |= {start + i for i in gate.sensitive}
    kept = [p for i, p in enumerate(kept) if i not in dropped]
    texts = [t for i, t in enumerate(texts) if i not in dropped]
    ctx.kept_count = len(kept)
    return kept, texts


def _dream_input(sections, kept, texts, instructions, now) -> str:
    current = (
        "\n\n".join(
            f"[{s.key or 'dynamic'}] {s.heading} ({s.origin}, last supported "
            f"{s.last_supported_at.date().isoformat()})\n{s.body}"
            for s in sections
        )
        or "(none)"
    )
    sessions: dict[str, str] = {}
    lines = []
    for i, (p, text) in enumerate(zip(kept, texts, strict=True), start=1):
        label = sessions.setdefault(p.session_id, f"S{len(sessions) + 1}")
        lines.append(f"[P{i}] [{label}] [{p.server_timestamp.date().isoformat()}] {text}")
    return (
        f"<today>{now.date().isoformat()}</today>\n"
        f"<current_profile>\n{current}\n</current_profile>\n"
        f"<instructions>{instructions or '(none)'}</instructions>\n"
        f"<allowed_core_slots>{', '.join(CORE_KEYS)}</allowed_core_slots>\n"
        f"<prompts>\n" + "\n".join(lines) + "\n</prompts>"
    )


def _parse_candidates(raw: str, count: int) -> list[_Candidate]:
    parsed = parse_json_object(raw)
    if not parsed or not isinstance(parsed.get("sections"), list):
        raise _Fail("failed", "bad_dream_output")
    out = []
    for entry in parsed["sections"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("body"), str):
            continue
        body = clean_text(entry["body"])
        if not body:
            continue  # nothing to say: the section is gone
        key = entry.get("key")
        if key is not None:
            if key not in CORE_KEYS:
                continue
            heading = CORE_HEADINGS[key]
        else:
            heading = clean_text(str(entry.get("heading") or ""))[:100]
            if not heading or is_sensitive(heading):
                continue
        support = entry.get("support")
        valid = sorted(
            {n for n in (support if isinstance(support, list) else []) if _is_index(n, count)}
        )
        out.append(
            _Candidate(
                key,
                heading,
                body,
                entry.get("origin") if entry.get("origin") in ORIGINS else "inferred",
                valid,
            )
        )
    return out


def _is_index(n, count: int) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= count


def _build_candidate(candidates, existing, kept, now):
    """Apply the code rules to the model's proposal. Returns (sections, unsupported heading|None)."""
    cutoff = now - timedelta(days=STALE_DAYS)
    by_heading = {normalize_heading(s.heading): s for s in existing}
    rows: list[SectionData] = []
    unsupported = None
    for c in candidates:
        if not c.support:
            unsupported = unsupported or c.heading
            continue
        supporting = [kept[n - 1] for n in c.support]
        if c.key is None and len({p.session_id for p in supporting}) < 2:
            continue  # a dynamic section needs support in two sessions
        supported = max(p.server_timestamp for p in supporting)
        prior = by_heading.get(normalize_heading(c.heading))
        if prior is not None and prior.origin == "stated" and c.origin == "inferred":
            c.body, c.origin = prior.body, "stated"  # stated beats inferred
        rows.append(
            SectionData(
                c.key, "core" if c.key else "dynamic", c.heading, c.body, c.origin, supported, 0
            )
        )
    # A section the user asked for in the Settings box has no prompt behind it; the dream keeps it
    # for as long as it is itself still fresh.
    have = {normalize_heading(r.heading) for r in rows}
    for s in existing:
        if (
            s.origin == "stated"
            and s.last_supported_at >= cutoff
            and (normalize_heading(s.heading) not in have)
        ):
            rows.append(
                SectionData(s.key, s.kind, s.heading, s.body, s.origin, s.last_supported_at, 0)
            )
    order = {k: i for i, k in enumerate(CORE_KEYS)}
    core = sorted((r for r in rows if r.kind == "core"), key=lambda r: order.get(r.key, 99))
    ordered = core + [r for r in rows if r.kind != "core"]
    for i, r in enumerate(ordered):
        r.position = i
    return ordered, unsupported


def _same_content(a: list[SectionData], b: list[SectionData]) -> bool:
    def view(rows):
        return [(r.key, r.kind, r.heading, r.body, r.origin) for r in rows]

    return view(a) == view(b)


def _dream_steps(
    ctx: _Ctx, now: datetime, llm: LLMClient, jev: JevClient | None, current_mark: datetime | None
) -> RunResult:
    existing = get_sections(ctx.user_id)
    ctx.profile_before = render_profile(existing)
    kept, texts = _dream_prompts(ctx, now, jev)
    if not kept and ctx.prompts:
        # The window has prompts but every one was screened out: that says nothing about what the
        # window supports, so nothing is written and the notice is left alone.
        return _finish(ctx, "no_change", "all_screened")
    if not kept and not existing:
        return _finish(ctx, "no_change", "no_new_prompts")

    candidates: list[_Candidate] = []
    if kept:
        _take_quota(ctx.user_id, now)
        instructions = clean_text(get_settings(ctx.user_id).instructions_text)
        user = _dream_input(existing, kept, texts, instructions, now)
        raw = _call(ctx, llm, load_chapter(DREAM_CHAPTER), user, 1500)
        candidates = _parse_candidates(raw, len(kept))
        ctx.sections = [
            _section_summary(
                c.key, "rebuild", c.origin, len({kept[n - 1].session_id for n in c.support})
            )
            for c in candidates
        ]
    final, unsupported = _build_candidate(candidates, existing, kept, now)
    if unsupported:
        raise _Fail("rejected", "unsupported_section", "unsupported_section")
    if _same_content(final, existing):
        # Nothing to write, but the window just confirmed these sections: keep their dates fresh
        # (no revision bump, no notice) so a later run does not drop them as stale.
        if not refresh_support_dates(
            ctx.user_id, ctx.base_revision, {s.heading: s.last_supported_at for s in final}
        ):
            raise _Fail("failed", REVISION_MOVED)
        return _finish(ctx, "no_change", "dream_unchanged")
    if final:
        result = check_profile(final, llm)  # a model call: outside any transaction
        if not result.ok:
            raise _Fail("rejected", result.failed or "check", result.failed or "")
    mark = max((t for t in (current_mark, ctx.watermark_to) if t is not None), default=None)
    if not replace_sections(
        ctx.user_id,
        ctx.base_revision,
        final,
        updated_by="la",
        processed_through=mark,
        set_notice=True,
    ):
        raise _Fail("failed", REVISION_MOVED)
    return _finish(ctx, "applied", None, profile_after=render_profile(final))


def _chapters(window: str) -> tuple[str, ...]:
    return (DREAM_CHAPTER,) if window == "dream" else (EXTRACTION_CHAPTER, WRITER_CHAPTER)


def _section_summary(key, action: str, origin: str, sessions: int) -> dict:
    """One proposed section as counts and codes: its core slot key, or "dynamic" for anything
    else (a heading is text the user's prompts shaped, so it is never kept)."""
    return {
        "key": key if key in CORE_KEYS else "dynamic",
        "action": action,
        "origin": origin,
        "sessions": sessions,
    }


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _summary(ctx: _Ctx) -> str:
    """What every run row keeps of the run, on every exit path: how many prompts were read and
    screened out, the Jev gate's source and scores, and the proposed sections as slot key
    (or "dynamic"), action, origin and session count. No notes, headings or model prose."""
    kept = len(ctx.prompts) if ctx.kept_count is None else ctx.kept_count
    gate = (
        {
            "source": str(ctx.gate.get("source")),
            "scores": {str(k): _number(v) for k, v in (ctx.gate.get("scores") or {}).items()},
        }
        if ctx.gate
        else None
    )
    return json.dumps(
        {
            "prompts_read": len(ctx.prompts),
            "screened_out": len(ctx.prompts) - kept,
            "gate": gate,
            "sections": ctx.sections,
        }
    )


def _finish(
    ctx: _Ctx, status: str, reason: str | None, failed_check: str = "", **extra
) -> RunResult:
    finished = timezone.now()
    run_id = record_run_guarded(
        expected_revision=ctx.base_revision + 1 if status == "applied" else ctx.base_revision,
        user_id=ctx.user_id,
        trigger=ctx.trigger,
        window=ctx.window,
        started_at=ctx.started_at,
        finished_at=finished,
        watermark_from=ctx.since,
        watermark_to=ctx.watermark_to,
        prompt_ids=[p.message_id for p in ctx.prompts],
        extraction=_summary(ctx),
        profile_before=ctx.profile_before,  # the stored Profile, which passed the checks
        # only an applied run stores it: it then equals the stored Profile. A rejected or failed
        # candidate is never persisted.
        profile_after=extra.get("profile_after", "") if status == "applied" else "",
        chapter_hashes={name: chapter_sha(name) for name in _chapters(ctx.window)},
        model=ctx.usage.model,
        input_tokens=ctx.usage.input_tokens or None,
        output_tokens=ctx.usage.output_tokens or None,
        cost_usd=round(ctx.usage.cost_usd, 6) if ctx.usage.input_tokens else None,
        status=status,
        failed_check=failed_check,
        reason=(reason or "")[:200],
    )
    return RunResult(status, reason, run_id)
