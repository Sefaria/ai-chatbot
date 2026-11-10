"""B. nightly: log prompts during the day, consolidate once per user in a batch.

The hot path makes no model call. The batch sends one consolidation call per active user and the
code enforces each slot's `min_sessions` on the validated evidence.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..signals import (
    cited_categories,
    detect_language,
    hebrew_ratio,
    is_sensitive,
    profile_language_v2,
    session_vote,
)
from ..types import Evidence, Observation, Turn
from .base import BatchResult, MemoryStrategy, TurnResult, truncate_prompt, verbatim_evidence
from .prompts import CONSOLIDATION_SYSTEM, current_profile_text, slot_definitions

logger = logging.getLogger(__name__)

WINDOW_DAYS = 30
MAX_OBSERVATIONS = 40


class NightlyStrategy(MemoryStrategy):
    name = "nightly"
    # Slots the consolidation call may touch (None: every non-deterministic slot).
    batch_slots: frozenset[str] | None = None

    def on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        if reason := self.turn_skip_reason(turn):
            return TurnResult(skipped_reason=reason)
        self.log_observation(turn)
        return TurnResult()

    def log_observation(self, turn: Turn, *, excluded: bool = False) -> None:
        """Append the prompt. `excluded` marks it out of every later window, input and evidence."""
        signals = {
            "language": detect_language(turn.user_message),
            "hebrew_ratio": round(hebrew_ratio(turn.user_message), 3),
            "cited_categories": cited_categories(turn.user_message),
        }
        if excluded:
            signals["excluded"] = True
        self.store.add_observation(
            Observation(
                user_id=turn.user_id,
                session_id=turn.session_id,
                message_id=turn.message_id,
                text=turn.user_message,
                at=turn.at,
                signals=signals,
            )
        )

    def window_observations(self, user_id: str, now: datetime) -> list[Observation]:
        """Observations of the last 30 days a model or a language vote may see."""
        return [
            o
            for o in self.store.observations(user_id, since=now - timedelta(days=WINDOW_DAYS))
            if not is_sensitive(o.text) and not o.signals.get("excluded")
        ]

    def run_batch(self, now: datetime, user_ids: list[str] | None = None) -> BatchResult:
        self.store.expire(now)  # before any profile is read, so stale slots are not merged back
        result = BatchResult()
        for user_id in self.store.users_with_pending() if user_ids is None else user_ids:
            try:
                self.consolidate(user_id, now, result)
            except Exception:
                logger.exception("consolidation failed for a user; observations stay unconsumed")
                result.errors += 1
        self.store.expire(now)
        return result

    def update_language(self, user_id: str, observations: list[Observation], now: datetime) -> bool:
        """preferred_language in code: per-session votes, then the v2 rule against the current value."""
        if not observations:
            return False
        sessions: dict[str, list[str]] = {}
        for obs in observations:
            sessions.setdefault(obs.session_id, []).append(obs.text)
        current = self.store.get_profile(user_id).get("preferred_language")
        language = profile_language_v2(
            [session_vote(texts) for texts in sessions.values()], current.value if current else None
        )
        if language is None:
            return False
        last = observations[-1]
        proof = verbatim_evidence(
            last.text.strip()[:80], last.text, last.session_id, last.message_id, last.at
        )
        return self.apply_update(
            user_id, "preferred_language", language, [proof] if proof else [], now
        )

    def blocked_slots(self, user_id: str) -> set[str]:
        """Slots the batch must leave alone (the hybrid strategy blocks pending proposals)."""
        return set()

    # -- consolidation ------------------------------------------------------------------

    def consolidate(self, user_id: str, now: datetime, result: BatchResult) -> None:
        if not self.store.get_settings(user_id).store_enabled:
            self.store.mark_consumed(user_id, now)
            return
        self.store.expire(now)
        recent = self.window_observations(user_id, now)[-MAX_OBSERVATIONS:]
        if not recent:
            self.store.mark_consumed(user_id, now)
            return
        blocked = self.blocked_slots(user_id)

        if "preferred_language" not in blocked and self.update_language(user_id, recent, now):
            result.updated_slots.append((user_id, "preferred_language"))

        seen = {o.message_id: o for o in recent}
        profile = self.store.get_profile(user_id)
        system = f"{CONSOLIDATION_SYSTEM}\n\nSlots:\n{slot_definitions(self.registry, only=self.batch_slots)}"
        user = (
            f"Current profile:\n{current_profile_text(profile)}\n\nRecent prompts by session:\n"
            f"{self.format_sessions(recent)}"
        )
        reply = self.call_model("batch", user_id, system, user)
        result.llm_calls += 1
        data = self.parse_reply(reply.text) or {}

        for update in data.get("updates") or []:
            if not isinstance(update, dict):
                continue
            slot = self.registry.get(str(update.get("slot")))
            if slot is None or slot.deterministic:
                self.diagnostics["value_invalid"] += 1
                continue
            if slot.key in blocked or (
                self.batch_slots is not None and slot.key not in self.batch_slots
            ):
                continue
            evidence = self.validated_evidence(update.get("evidence"), seen)
            if not evidence:
                self.diagnostics["no_valid_evidence"] += 1
                continue
            if len({e.session_id for e in evidence}) < slot.min_sessions:
                self.diagnostics["below_min_sessions"] += 1
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
                and (self.batch_slots is None or key in self.batch_slots)
                and key not in blocked
            ):
                self.store.delete_slot(user_id, key)

        self.store.mark_consumed(user_id, now)
        result.users_processed += 1

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

    def validated_evidence(self, raw: object, seen: dict[str, Observation]) -> list[Evidence]:
        """Keep only quotes that are verbatim substrings of the message they cite."""
        evidence = []
        for item in raw if isinstance(raw, list) else []:
            obs = seen.get(str(item.get("message_id"))) if isinstance(item, dict) else None
            if obs is None:
                self.diagnostics["quote_not_verbatim"] += 1  # cites a message it was not shown
                continue
            proof = self.model_evidence(
                item.get("quote"), truncate_prompt(obs.text), obs.session_id, obs.message_id, obs.at
            )
            if proof:
                evidence.append(proof)
        return evidence
