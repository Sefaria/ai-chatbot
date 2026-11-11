"""A. realtime: one extraction call per turn, applied at once, no thresholds.

This is the deliberately aggressive path: it writes after a single turn, so it shows how fast and
how noisy per-turn memory is. `preferred_language` is computed by code, not asked of the model.
The model sees only the learner's own prompt: the assistant reply is never sent to it.
"""

from __future__ import annotations

from datetime import datetime

from ..signals import detect_language, find_standing_instructions
from ..types import Turn
from .base import BatchResult, MemoryStrategy, TurnResult, truncate_prompt, verbatim_evidence
from .prompts import REALTIME_SYSTEM, current_profile_text, slot_definitions


class RealtimeStrategy(MemoryStrategy):
    name = "realtime"

    def _on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        if reason := self.turn_skip_reason(turn):
            return TurnResult(skipped_reason=reason)
        self.store.expire_user(turn.user_id, now)  # a stale slot must not reach the model
        # Nothing is consolidated later: the ledger row is a content-free marker, stored consumed.
        # A turn that holds a standing instruction is quarantined and never sent to the model: this
        # strategy has no classifier to clear it.
        quarantine = bool(find_standing_instructions(turn.user_message))
        if reason := self.start_turn(
            turn, now, excluded=True, consumed=True, quarantined=quarantine
        ):
            return TurnResult(skipped_reason=reason)
        if quarantine:
            return TurnResult(skipped_reason="instruction_quarantined")
        message = truncate_prompt(turn.user_message)
        result = TurnResult()

        language = detect_language(message)  # a 'mixed' prompt casts no vote
        proof = verbatim_evidence(
            message.strip()[:80], message, turn.session_id, turn.message_id, now
        )
        if language in ("en", "he") and self.apply_update(
            turn.user_id, "preferred_language", language, [proof] if proof else [], now
        ):
            result.updated_slots.append("preferred_language")
        if self.is_stale(turn.user_id):
            return result

        system = f"{REALTIME_SYSTEM}\n\nSlots:\n{slot_definitions(self.registry)}"
        user = (
            f"Current profile:\n{current_profile_text(self.store.get_profile(turn.user_id))}\n\n"
            f"This turn:\nLearner: {message}"
        )
        reply = self.call_model("turn", turn.user_id, system, user)
        result.llm_calls = 1
        parsed = self.parse_reply(reply.text)
        if parsed is not None:
            parsed = self.envelope(parsed, {"updates": list}, allow_extra=True)
            if parsed is None:
                result.errors += 1
        for update in (parsed or {}).get("updates") or []:
            if self.is_stale(turn.user_id):
                break
            if not isinstance(update, dict):
                continue
            slot = self.registry.get(str(update.get("slot")))
            if slot is None or slot.deterministic:
                self.diagnostics["value_invalid"] += 1
                continue
            evidence = self.model_evidence(
                update.get("quote"), message, turn.session_id, turn.message_id, now
            )
            if self.apply_update(
                turn.user_id, slot.key, update.get("value"), [evidence] if evidence else [], now
            ):
                result.updated_slots.append(slot.key)
        return result

    def _run_batch(self, now: datetime, user_ids: list[str] | None = None) -> BatchResult:
        self.store.expire(now)
        return BatchResult()
