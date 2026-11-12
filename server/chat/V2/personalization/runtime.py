"""Process-level plumbing for the request path: what personalization adds to a turn. Nothing here
may fail a chat turn."""

from __future__ import annotations

import logging
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..prompts import chapters
from . import gate, profile_store
from .parsing import escape_block

logger = logging.getLogger(__name__)

CHAPTER = "personalization_answering"


@dataclass
class PersonalizationContext:
    """The blocks a turn's prompt gets from personalization, plus its trace metadata."""

    instructions_text: str | None = None
    profile_text: str | None = None
    meta: dict = field(default_factory=dict)
    notice: bool = False


def notice_for_message(user_id: str, message_id: str | None) -> bool:
    """Whether the turn that answered this user message showed the one-time notice. Kept in the
    database (not a per-process cache) so the recover path can repeat it from any worker."""
    try:
        return profile_store.notice_shown_for(user_id, message_id)
    except Exception:
        return False


def context_for_request(
    user_id: str, *, plain: bool, message_id: str | None = None
) -> PersonalizationContext:
    """What to add to this turn's prompt: the user's Instructions and Profile, each only when its
    switch is on and its text is not empty. `plain=True` (the plain arm of the dual display) never
    returns blocks and never consumes the notice. Any failure gives an unpersonalized turn."""
    if plain or not gate.enabled_for(user_id):
        return PersonalizationContext()
    try:
        settings_row = profile_store.get_settings(user_id)
        instructions = (
            escape_block(settings_row.instructions_text or "")
            if settings_row.instructions_enabled
            else ""
        )
        profile = ""
        if settings_row.profile_enabled:
            profile = escape_block(
                profile_store.render_profile(profile_store.get_sections(user_id))
            )
        if not instructions and not profile:
            return PersonalizationContext()

        notice = profile_store.take_notice(user_id, message_id)
        meta = {
            "personalized": True,
            "profile_on": settings_row.profile_enabled,
            "instructions_on": settings_row.instructions_enabled,
            "profile_revision": settings_row.profile_revision,
            "chapter_sha": chapters.chapter_sha(CHAPTER),
            "notice_shown": notice,
        }
        if gate.lab_enabled():
            meta["profile_injected"] = profile
            meta["instructions_injected"] = instructions
        return PersonalizationContext(
            instructions_text=instructions or None,
            profile_text=profile or None,
            meta=meta,
            notice=notice,
        )
    except Exception:
        logger.warning("Personalization failed; answering without it", exc_info=True)
        return PersonalizationContext()


def update_every_message(user_id: str) -> bool:
    """The user's lab testing switch: run the job after every reply."""
    try:
        return bool(profile_store.get_settings(user_id).update_every_message)
    except Exception:
        return False


_POOL_WORKERS = 2
_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
_user_queues: dict[str, deque[str]] = {}  # users with a drain task queued or running
_idle = threading.Condition()
_pending = 0


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=_POOL_WORKERS, thread_name_prefix="personalization-job"
            )
        return _pool


def _open_span(user_id: str, trigger: str):
    """One Braintrust span per job run, only when a logger is already initialised (never creates
    one, never needs credentials). Metadata is the trigger, the run id and the status: no text."""
    try:
        import braintrust

        bt_logger = braintrust.current_logger()
        if bt_logger is None:
            return None
        return bt_logger.start_span(name="personalization-job", metadata={"trigger": trigger})
    except Exception:
        return None


def _close_span(span, result) -> None:
    if span is None:
        return
    try:
        span.log(
            metadata={
                "run_id": getattr(result, "run_id", None),
                "status": getattr(result, "status", "error"),
            }
        )
        span.end()
    except Exception:
        logger.debug("Could not close the personalization job span", exc_info=True)


def _run_job(user_id: str, trigger: str) -> None:
    from . import profile_job

    result = None
    span = None
    try:
        span = _open_span(user_id, trigger)
        result = profile_job.run_profile_job(user_id, trigger)
    except Exception:
        logger.warning("Personalization job failed (trigger=%s)", trigger, exc_info=True)
    finally:
        _close_span(span, result)
        try:
            from django.db import connections

            connections.close_all()
        except Exception:
            logger.debug("Could not close database connections", exc_info=True)


def _drain(user_id: str) -> None:
    """Run one user's queued triggers in the order they came in, then let the user go."""
    global _pending
    while True:
        with _pool_lock:
            queue = _user_queues[user_id]
            if not queue:
                del _user_queues[user_id]
                return
            trigger = queue.popleft()
        try:
            _run_job(user_id, trigger)
        finally:
            with _idle:
                _pending -= 1
                _idle.notify_all()


def queue_job(user_id: str, trigger: str) -> None:
    """Queue a Profile job on a small background pool (2 workers). One user's jobs run one at a
    time, in the order queued. Never blocks the reply and never raises."""
    global _pending
    try:
        with _idle:
            _pending += 1
        with _pool_lock:
            queue = _user_queues.get(user_id)
            if queue is not None:
                queue.append(trigger)  # a worker is already draining this user
                return
            _user_queues[user_id] = deque([trigger])
        try:
            _get_pool().submit(_drain, user_id)
        except Exception:
            with _pool_lock:
                _user_queues.pop(user_id, None)
            raise
    except Exception:
        with _idle:
            _pending = max(0, _pending - 1)
            _idle.notify_all()
        logger.warning("Could not queue the personalization job", exc_info=True)


def wait_for_idle(timeout: float = 10.0) -> bool:
    """Block until every queued job has finished (for tests). False on timeout."""
    with _idle:
        return _idle.wait_for(lambda: _pending == 0, timeout)
