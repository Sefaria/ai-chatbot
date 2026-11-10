"""A. realtime: one extraction call per turn, applied at once, no thresholds.

This is the deliberately aggressive path: it writes after a single turn, so it shows how fast and
how noisy per-turn memory is. `preferred_language` is computed by code, not asked of the model.
"""

from __future__ import annotations

from datetime import datetime

from ..parsing import parse_json_object
from ..signals import detect_language
from ..types import Turn
from .base import BatchResult, MemoryStrategy, TurnResult, truncate_prompt, verbatim_evidence
from .prompts import REALTIME_SYSTEM, current_profile_text, slot_definitions

ASSISTANT_CHARS = 500


class RealtimeStrategy(MemoryStrategy):
    name = "realtime"

    def on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        if reason := self.turn_skip_reason(turn):
            return TurnResult(skipped_reason=reason)
        message = truncate_prompt(turn.user_message)
        result = TurnResult()

        language = detect_language(message)
        proof = verbatim_evidence(
            message.strip()[:80], message, turn.session_id, turn.message_id, now
        )
        if language and self.apply_update(
            turn.user_id, "preferred_language", language, [proof] if proof else [], now
        ):
            result.updated_slots.append("preferred_language")

        system = f"{REALTIME_SYSTEM}\n\nSlots:\n{slot_definitions(self.registry)}"
        user = (
            f"Current profile:\n{current_profile_text(self.store.get_profile(turn.user_id))}\n\n"
            f"This turn:\nLearner: {message}\nAssistant: {turn.assistant_response[:ASSISTANT_CHARS]}"
        )
        reply = self.call_model("turn", turn.user_id, system, user)
        result.llm_calls = 1
        for update in (parse_json_object(reply.text) or {}).get("updates") or []:
            if not isinstance(update, dict):
                continue
            slot = self.registry.get(str(update.get("slot")))
            if slot is None or slot.deterministic:
                continue
            evidence = verbatim_evidence(
                update.get("quote"), message, turn.session_id, turn.message_id, now
            )
            if self.apply_update(
                turn.user_id, slot.key, update.get("value"), [evidence] if evidence else [], now
            ):
                result.updated_slots.append(slot.key)
        return result

    def run_batch(self, now: datetime, user_ids: list[str] | None = None) -> BatchResult:
        self.store.expire(now)
        return BatchResult()
