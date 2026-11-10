"""Process-level plumbing for the request path: build the service once, read the profile for a
request, run the after-turn update off the request thread. Nothing here may fail a chat turn."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime

from django.conf import settings
from django.db import close_old_connections, connections, transaction

from chat.V2.sentry import capture_exception

from .gate import PersonalizationConfig
from .service import PersonalizationService, ProfileContext
from .types import Turn

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_store = None
_registry = None
_services: dict[str, PersonalizationService] = {}

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="personalization")
_user_locks: dict[str, list] = {}  # store_user_id -> [lock, holders]
_user_locks_guard = threading.Lock()
_pending: set[Future] = set()
_pending_guard = threading.Lock()


def get_store():
    global _store
    with _lock:
        if _store is None:
            from .store import DjangoProfileStore

            _store = DjangoProfileStore()
        return _store


def get_registry():
    global _registry
    with _lock:
        if _registry is None:
            from .registry import SlotRegistry

            _registry = SlotRegistry.load()
        return _registry


def get_service(mode: str) -> PersonalizationService | None:
    """Built lazily and cached per mode; `None` when the mode is off."""
    if mode == "off":
        return None
    store, registry = get_store(), get_registry()
    with _lock:
        service = _services.get(mode)
        if service is None:
            from .jev import HttpJevClient
            from .llm import AnthropicLLMClient, UsageLedger
            from .strategies.base import build_strategy

            strategy = build_strategy(
                mode,
                store,
                registry,
                AnthropicLLMClient(),
                UsageLedger(),
                settings.PERSONALIZATION_MODEL,
                jev=HttpJevClient() if mode == "hybrid_jev" else None,
            )
            service = PersonalizationService(store, registry, strategy, mode=mode)
            _services[mode] = service
        return service


def profile_for_request(config: PersonalizationConfig, now: datetime) -> ProfileContext | None:
    """The profile to render for this request, or None. Never raises: a failure (a missing table
    included) is reported and the turn goes on unpersonalized."""
    try:
        if config.fixture_profile is not None:
            service = PersonalizationService(get_store(), get_registry(), None, mode=config.mode)
            return service.render_fixture(config.fixture_profile, now)
        service = get_service(config.mode)
        if service is None:
            return None
        with transaction.atomic():  # a savepoint: a failed read cannot poison the request
            return service.profile_snapshot(config.store_user_id, now)
    except Exception as exc:
        logger.exception("Personalization profile read failed; continuing unpersonalized")
        capture_exception(exc, phase="personalization_profile_read")
        return None


def _user_lock(user_id: str) -> threading.Lock:
    with _user_locks_guard:
        entry = _user_locks.setdefault(user_id, [threading.Lock(), 0])
        entry[1] += 1
        return entry[0]


def _release_user_lock(user_id: str) -> None:
    with _user_locks_guard:
        entry = _user_locks[user_id]
        entry[1] -= 1
        if entry[1] == 0:
            del _user_locks[user_id]


def _log_update_span(parent_export: str | None, config, result) -> None:
    if not parent_export or result is None:
        return
    import braintrust

    with braintrust.start_span(name="personalization-update", parent=parent_export) as span:
        span.log(
            metadata={
                "mode": config.mode,
                "model_calls": result.llm_calls,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_usd": result.cost_usd,
                "writes_applied": result.writes_applied,
                "writes_refused": dict(result.writes_refused),
                "diagnostics": dict(result.diagnostics),
            }
        )


def _run_after_turn(
    config, turn: Turn, now: datetime, parent_export: str | None, user_lock
) -> None:
    try:
        close_old_connections()
        with user_lock:
            service = get_service(config.mode)
            result = service.after_turn(turn, now) if service else None
        try:
            _log_update_span(parent_export, config, result)
        except Exception:
            logger.debug("Could not log the personalization-update span", exc_info=True)
    except Exception as exc:
        logger.exception("Personalization after-turn update failed")
        capture_exception(exc, phase="personalization_after_turn")
    finally:
        try:
            close_old_connections()
            connections.close_all()  # the pool thread owns its own connections; leave none open
        finally:
            _release_user_lock(turn.user_id)


def submit_after_turn(
    config: PersonalizationConfig,
    turn: Turn,
    parent_span_export: str | None,
    now: datetime | None = None,
) -> Future | None:
    """Queue the memory update for a saved turn. Jobs of one learner run one at a time, in
    submission order within this process. Never raises."""
    try:
        from django.utils import timezone

        user_lock = _user_lock(turn.user_id)
        try:
            future = _executor.submit(
                _run_after_turn, config, turn, now or timezone.now(), parent_span_export, user_lock
            )
        except Exception:
            _release_user_lock(turn.user_id)
            raise
        with _pending_guard:
            _pending.add(future)
        future.add_done_callback(_forget)
        return future
    except Exception as exc:
        logger.exception("Could not queue the personalization update")
        capture_exception(exc, phase="personalization_submit")
        return None


def _forget(future: Future) -> None:
    with _pending_guard:
        _pending.discard(future)


def wait_for_idle(timeout: float = 10.0) -> bool:
    """Wait for queued updates (tests and shutdown). True when none are left."""
    with _pending_guard:
        pending = list(_pending)
    for future in pending:
        try:
            future.result(timeout=timeout)
        except Exception:
            pass
    return all(f.done() for f in pending)


def move_profile(from_user_id: str, to_user_id: str) -> None:
    """Move an anonymous visitor's profile to the registered user. Call inside the claim's
    transaction; its own savepoint keeps a failure from poisoning the claim."""
    try:
        with transaction.atomic():
            get_store().move(from_user_id, to_user_id)
    except Exception as exc:
        logger.exception("Personalization profile move failed; the claim goes on")
        capture_exception(exc, phase="personalization_claim_move")
