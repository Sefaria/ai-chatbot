"""The Profile job: one nightly `profile_update` call builds the whole Profile.

One function, `run_profile_job`, serves every trigger. Rules kept in code:

- one job at a time per user (the lock); a busy lock skips the run, the next trigger picks it up
- the rule-based sensitive screen and the Jev sensitive gate run before anything reaches the model
- the model's JSON is validated (`update_io`); an invalid reply is retried once, then the current
  Profile is kept unchanged
- core headings, support dates and origins are set from code: `user` beats `stated` beats
  `inferred`, and a stored `user` section survives without prompt support
- the never-store check runs per section and drops only the offending sections
- a new revision is saved only when the sections changed; `last_run_at` is set on every completed run
- model and Jev calls happen outside any database transaction
- a run row holds no free text derived from the user: `extraction` is a counts-and-codes summary
  (`_summary`), `profile_after` is stored only for an applied run (it then equals the stored
  Profile), and `reason` and `failed_check` are fixed codes
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from chat.V2.prompts.chapters import chapter_source, load_chapter

from . import jev_questions as q
from . import quota
from .checks import check_profile, normalize_heading
from .core_slots import CORE_KEYS
from .jev import JevClient
from .jev_gate import ask_gate
from .llm import AnthropicLLMClient, LLMClient
from .profile_store import (
    PromptRow,
    SectionData,
    get_sections,
    get_settings,
    mark_run,
    record_run_guarded,
    refresh_support_dates,
    render_profile,
    replace_sections,
)
from .update_io import (
    ORIGINS,
    UpdateOutputError,
    build_update_input,
    support_dates,
    validate_update_output,
)

logger = logging.getLogger(__name__)

UPDATE_CHAPTER = "profile_update"
UPDATE_MAX_TOKENS = 1500
STALE_DAYS = 60  # the window of the retiring dream command
REVISION_MOVED = "revision_moved"
PROFILE_OFF = "profile_off"
# Run reasons of the single update path (fixed codes).
VALID_EMPTY = "valid_empty"
RETRY_SUCCEEDED = "retry_succeeded"
VALIDATION_FAILED_KEPT_CURRENT = "validation_failed_kept_current"
NEVER_STORE_REJECTED = "never_store_rejected"
NO_PROMPTS = "no_prompts"


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
    started_at: datetime
    base_revision: int
    prompts: list[PromptRow] = field(default_factory=list)
    # What the run row may keep about the run: counts and codes, never text (see `_summary`).
    gate: dict | None = None
    sections: list[dict] = field(default_factory=list)
    profile_before: str = ""
    usage: _Usage = field(default_factory=_Usage)


class _Fail(Exception):
    def __init__(self, status: str, reason: str, failed_check: str = ""):
        super().__init__(reason)
        self.status, self.reason, self.failed_check = status, reason, failed_check


def run_profile_job(
    user_id: str,
    trigger: str,
    *,
    window: str | None = None,  # accepted and ignored for one release: there is one path now
    now: datetime | None = None,
    llm: LLMClient | None = None,
    jev: JevClient | None = None,
) -> RunResult:
    if not get_settings(user_id).profile_enabled:
        return RunResult("no_change", PROFILE_OFF)  # before any read, model or Jev call; no row
    now = now or timezone.now()
    lease = quota.acquire_job_lock(user_id, now)
    if not lease:
        return RunResult("busy", "job_running")
    try:
        return _run(user_id, trigger, now, llm or AnthropicLLMClient(), jev)
    finally:
        quota.release_job_lock(user_id, lease)


def _run(user_id, trigger, now, llm, jev) -> RunResult:
    ctx = _Ctx(user_id, trigger, now, get_settings(user_id).profile_revision)
    try:
        return _steps(ctx, now, llm, jev)
    except _Fail as fail:
        if fail.status == "quota":
            return RunResult("quota", fail.reason)
        return _finish(ctx, fail.status, fail.reason, fail.failed_check)
    except Exception as exc:  # a model or store error: the run fails, the Profile stays
        logger.warning("profile job failed: %s", type(exc).__name__)
        return _finish(ctx, "failed", f"error:{type(exc).__name__}")


def _steps(ctx: _Ctx, now: datetime, llm: LLMClient, jev: JevClient | None) -> RunResult:
    existing = get_sections(ctx.user_id)
    ctx.profile_before = render_profile(existing)
    text, prompts = build_update_input(
        ctx.user_id, now, screen=lambda rows: _jev_screen(ctx, rows, jev)
    )
    ctx.prompts = prompts
    if not prompts:
        return _finish(ctx, "no_change", NO_PROMPTS)

    _take_quota(ctx.user_id, now)
    returned, retried = _ask_model(ctx, llm, text, len(prompts))
    ctx.sections = [_section_summary(s["key"], s["origin"], len(s["support"])) for s in returned]
    final = _assemble(existing, returned, prompts)
    final, failed = _screen_sections(final, existing, llm)  # model calls: outside a transaction

    reason = (
        VALID_EMPTY
        if not returned
        else NEVER_STORE_REJECTED
        if failed
        else RETRY_SUCCEEDED
        if retried
        else None
    )
    if _same_content(final, existing):
        # Nothing to write, but the prompts just confirmed these sections: keep their dates fresh
        # (no revision bump, no notice) so the next run's input shows when they were last supported.
        if not refresh_support_dates(
            ctx.user_id, ctx.base_revision, {s.heading: s.last_supported_at for s in final}
        ):
            raise _Fail("failed", REVISION_MOVED)
        return _finish(ctx, "no_change", reason, failed)
    if not replace_sections(
        ctx.user_id,
        ctx.base_revision,
        final,
        updated_by="la",
        processed_through=None,
        set_notice=True,
    ):
        raise _Fail("failed", REVISION_MOVED)
    return _finish(ctx, "applied", reason, failed, profile_after=render_profile(final))


def _jev_screen(ctx: _Ctx, rows: list[PromptRow], jev: JevClient | None) -> list[PromptRow]:
    """Drop the prompts Jev's gate calls sensitive, a chunk at a time. Relevance and slots are not
    used: the update prompt decides what the prompts support."""
    dropped: set[int] = set()
    step = q.MAX_PROMPTS_PER_RUN
    for start in range(0, len(rows), step):
        gate = ask_gate(
            [p.text for p in rows[start : start + step]], settings.PERSONALIZATION_JEV_GATE, jev
        )
        ctx.gate = {"source": gate.source, "scores": gate.scores}
        dropped |= {start + i for i in gate.sensitive}
    return [p for i, p in enumerate(rows) if i not in dropped]


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


def _ask_model(ctx: _Ctx, llm: LLMClient, user: str, n_prompts: int) -> tuple[list[dict], bool]:
    """The validated sections and whether the retry was needed. A second invalid reply fails the
    run, which keeps the current Profile."""
    system = load_chapter(UPDATE_CHAPTER)
    for attempt in range(2):
        raw = _call(ctx, llm, system, user, UPDATE_MAX_TOKENS)
        try:
            return validate_update_output(raw, n_prompts), attempt == 1
        except UpdateOutputError as exc:
            error = exc
    raise _Fail("failed", VALIDATION_FAILED_KEPT_CURRENT, error.reason)


def _slot(key: str | None, heading: str) -> tuple:
    """What identifies a section across runs: its core key, or its normalized dynamic heading."""
    return ("core", key) if key else ("dynamic", normalize_heading(heading))


def _assemble(
    existing: list[SectionData], returned: list[dict], prompts: list[PromptRow]
) -> list[SectionData]:
    """The new section list from the validated output. `user` beats `stated` beats `inferred`; a
    stored `user` section survives an output that omits it; core slots come first, in slot order."""
    stored = {_slot(s.key, s.heading): s for s in existing}
    out: dict[tuple, SectionData] = {}
    for r in returned:
        slot = _slot(r["key"], r["heading"])
        prior = stored.get(slot)
        if prior is not None and prior.origin == "user" and r["origin"] != "stated":
            out[slot] = prior  # kept as it is: only an outright later statement changes it
        elif r["origin"] == "user":
            continue  # a `user` claim with nothing stored behind it
        elif prior is not None and prior.origin == "stated" and r["origin"] == "inferred":
            out[slot] = prior
        else:
            kind = "core" if r["key"] else "dynamic"
            out[slot] = SectionData(
                r["key"],
                kind,
                r["heading"],
                r["body"],
                r["origin"],
                support_dates(r, prompts),
                0,
            )
    for slot, s in stored.items():
        if s.origin == "user" and slot not in out:
            out[slot] = s  # survives without prompt support
    return _ordered(list(out.values()))


def _ordered(rows: list[SectionData]) -> list[SectionData]:
    order = {k: i for i, k in enumerate(CORE_KEYS)}
    core = sorted((s for s in rows if s.kind == "core"), key=lambda s: order.get(s.key, 99))
    ordered = core + [s for s in rows if s.kind != "core"]
    return [
        SectionData(s.key, s.kind, s.heading, s.body, s.origin, s.last_supported_at, i)
        for i, s in enumerate(ordered)
    ]


def _screen_sections(
    final: list[SectionData], existing: list[SectionData], llm: LLMClient
) -> tuple[list[SectionData], str]:
    """The never-store check per section, only for sections that are new or changed. Returns the
    kept sections and the first failed check code ("" when none failed)."""
    known = {(s.key, s.heading, s.body, s.origin) for s in existing}
    kept: list[SectionData] = []
    failed = ""
    for s in final:
        if (s.key, s.heading, s.body, s.origin) not in known:
            result = check_profile([s], llm)
            if not result.ok:
                failed = failed or result.failed or "check"
                continue
        kept.append(s)
    return _ordered(kept), failed


def _same_content(a: list[SectionData], b: list[SectionData]) -> bool:
    def view(rows):
        return [(r.key, r.kind, r.heading, r.body, r.origin) for r in rows]

    return view(a) == view(b)


def _section_summary(key, origin: str, support: int) -> dict:
    """One returned section as counts and codes: its core slot key, or "dynamic" for anything
    else (a heading is text the user's prompts shaped, so it is never kept)."""
    return {
        "key": key if key in CORE_KEYS else "dynamic",
        "origin": origin if origin in ORIGINS else "inferred",
        "support": support,
    }


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _summary(ctx: _Ctx) -> str:
    """What every run row keeps of the run, on every exit path: how many prompts reached the model,
    the Jev gate's source and scores, and the returned sections as slot key (or "dynamic"), origin
    and support count. No notes, headings or model prose."""
    gate = (
        {
            "source": str(ctx.gate.get("source")),
            "scores": {str(k): _number(v) for k, v in (ctx.gate.get("scores") or {}).items()},
        }
        if ctx.gate
        else None
    )
    return json.dumps({"prompts_read": len(ctx.prompts), "gate": gate, "sections": ctx.sections})


def _finish(
    ctx: _Ctx, status: str, reason: str | None, failed_check: str = "", **extra
) -> RunResult:
    if status in ("applied", "no_change") or reason == VALIDATION_FAILED_KEPT_CURRENT:
        mark_run(ctx.user_id, ctx.started_at)  # a completed run, even one that kept the Profile
    run_id = record_run_guarded(
        expected_revision=ctx.base_revision + 1 if status == "applied" else ctx.base_revision,
        user_id=ctx.user_id,
        trigger=ctx.trigger,
        started_at=ctx.started_at,
        finished_at=timezone.now(),
        prompt_ids=[p.message_id for p in ctx.prompts],
        extraction=_summary(ctx),
        profile_before=ctx.profile_before,  # the stored Profile, which passed the checks
        # only an applied run stores it: it then equals the stored Profile. A failed run never
        # persists a candidate.
        profile_after=extra.get("profile_after", "") if status == "applied" else "",
        chapter_hashes={UPDATE_CHAPTER: chapter_source(UPDATE_CHAPTER)},
        model=ctx.usage.model,
        input_tokens=ctx.usage.input_tokens or None,
        output_tokens=ctx.usage.output_tokens or None,
        cost_usd=round(ctx.usage.cost_usd, 6) if ctx.usage.input_tokens else None,
        status=status,
        failed_check=failed_check[:40],
        reason=(reason or "")[:200],
    )
    return RunResult(status, reason, run_id)
