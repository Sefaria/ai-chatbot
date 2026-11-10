"""B. nightly: log prompts during the day, consolidate once per user in a batch.

The hot path makes no model call. The batch sends one consolidation call per active user and the
code enforces each slot's `min_sessions` on the validated evidence.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..parsing import parse_json_object
from ..signals import (
    cited_categories,
    detect_language,
    hebrew_ratio,
    is_sensitive,
    profile_language,
    session_language,
)
from ..types import Evidence, Observation, Turn
from .base import BatchResult, MemoryStrategy, TurnResult, truncate_prompt, verbatim_evidence
from .prompts import CONSOLIDATION_SYSTEM, current_profile_text, slot_definitions

logger = logging.getLogger(__name__)

WINDOW_DAYS = 30
MAX_OBSERVATIONS = 40


class NightlyStrategy(MemoryStrategy):
    name = "nightly"

    def on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        if reason := self.turn_skip_reason(turn):
            return TurnResult(skipped_reason=reason)
        self.log_observation(turn)
        return TurnResult()

    def log_observation(self, turn: Turn) -> None:
        self.store.add_observation(
            Observation(
                user_id=turn.user_id,
                session_id=turn.session_id,
                message_id=turn.message_id,
                text=turn.user_message,
                at=turn.at,
                signals={
                    "language": detect_language(turn.user_message),
                    "hebrew_ratio": round(hebrew_ratio(turn.user_message), 3),
                    "cited_categories": cited_categories(turn.user_message),
                },
            )
        )

    def run_batch(self, now: datetime, user_ids: list[str] | None = None) -> BatchResult:
        result = BatchResult()
        for user_id in self.store.users_with_pending() if user_ids is None else user_ids:
            try:
                self.consolidate(user_id, now, result)
            except Exception:
                logger.exception("consolidation failed for a user; observations stay unconsumed")
                result.errors += 1
        self.store.expire(now)
        return result

    def blocked_slots(self, user_id: str) -> set[str]:
        """Slots the batch must leave alone (the hybrid strategy blocks pending proposals)."""
        return set()

    # -- consolidation ------------------------------------------------------------------

    def consolidate(self, user_id: str, now: datetime, result: BatchResult) -> None:
        if not self.store.get_settings(user_id).store_enabled:
            self.store.mark_consumed(user_id, now)
            return
        recent = [
            o
            for o in self.store.observations(user_id, since=now - timedelta(days=WINDOW_DAYS))
            if not is_sensitive(o.text)
        ][-MAX_OBSERVATIONS:]
        if not recent:
            self.store.mark_consumed(user_id, now)
            return
        blocked = self.blocked_slots(user_id)

        if "preferred_language" not in blocked:
            self.set_language_from(user_id, recent, now, result)

        seen = {o.message_id: o for o in recent}
        profile = self.store.get_profile(user_id)
        system = f"{CONSOLIDATION_SYSTEM}\n\nSlots:\n{slot_definitions(self.registry)}"
        user = (
            f"Current profile:\n{current_profile_text(profile)}\n\nRecent prompts by session:\n"
            f"{self.format_sessions(recent)}"
        )
        reply = self.call_model("batch", user_id, system, user)
        result.llm_calls += 1
        data = parse_json_object(reply.text) or {}

        for update in data.get("updates") or []:
            if not isinstance(update, dict):
                continue
            slot = self.registry.get(str(update.get("slot")))
            if slot is None or slot.deterministic or slot.key in blocked:
                continue
            evidence = self.validated_evidence(update.get("evidence"), seen)
            if len({e.session_id for e in evidence}) < slot.min_sessions:
                continue
            if self.apply_update(user_id, slot.key, update.get("value"), evidence, now):
                result.updated_slots.append((user_id, slot.key))

        for key in data.get("clear") or []:
            current = profile.get(key) if isinstance(key, str) else None
            slot = self.registry.get(key) if isinstance(key, str) else None
            if (
                current
                and current.status == "inferred"
                and not slot.deterministic
                and key not in blocked
            ):
                self.store.delete_slot(user_id, key)

        self.store.mark_consumed(user_id, now)
        result.users_processed += 1

    def set_language_from(
        self, user_id: str, observations: list[Observation], now: datetime, result: BatchResult
    ) -> None:
        """preferred_language is computed in code from per-session languages."""
        sessions: dict[str, list[Observation]] = {}
        for obs in observations:
            sessions.setdefault(obs.session_id, []).append(obs)
        language = profile_language(
            [session_language([o.text for o in rows]) for rows in sessions.values()]
        )
        last = observations[-1]
        proof = verbatim_evidence(
            last.text.strip()[:80], last.text, last.session_id, last.message_id, last.at
        )
        if language and self.apply_update(
            user_id, "preferred_language", language, [proof] if proof else [], now
        ):
            result.updated_slots.append((user_id, "preferred_language"))

    @staticmethod
    def format_sessions(observations: list[Observation]) -> str:
        sessions: dict[str, list[Observation]] = {}
        for obs in observations:
            sessions.setdefault(obs.session_id, []).append(obs)
        blocks = []
        for session_id, rows in sessions.items():
            lines = [f"Session {session_id} ({rows[0].at:%Y-%m-%d}):"]
            lines += [f"  [{o.message_id}] {truncate_prompt(o.text)}" for o in rows]
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    @staticmethod
    def validated_evidence(raw: object, seen: dict[str, Observation]) -> list[Evidence]:
        """Keep only quotes that are verbatim substrings of the message they cite."""
        evidence = []
        for item in raw if isinstance(raw, list) else []:
            obs = seen.get(str(item.get("message_id"))) if isinstance(item, dict) else None
            if obs is None:
                continue
            proof = verbatim_evidence(
                item.get("quote"), truncate_prompt(obs.text), obs.session_id, obs.message_id, obs.at
            )
            if proof:
                evidence.append(proof)
        return evidence
