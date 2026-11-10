"""C. hybrid: deterministic slots at once, standing instructions as proposals, batch for the rest.

A standing instruction ("from now on ...") never becomes a silent write: it becomes a pending
proposal the learner confirms into a stated slot. The batch is the nightly consolidation, minus
slots that have a pending proposal.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..parsing import parse_json_object
from ..registry import SlotRegistry
from ..signals import find_standing_instruction, profile_language, session_language
from ..store import ProfileStore
from ..types import Proposal, Turn, Value
from .base import TurnResult, truncate_prompt, verbatim_evidence
from .nightly import WINDOW_DAYS, NightlyStrategy
from .prompts import INSTRUCTION_SYSTEM, slot_definitions

PROPOSE_MEMORY_UPDATE_TOOL: dict[str, Any] = {
    "name": "propose_memory_update",
    "surfaces": ("agent",),
    "description": (
        "Use when the learner gives a standing instruction about how you should answer them "
        "('from now on...', 'always...', 'never...'). Proposes saving it to their profile; "
        "the learner confirms before anything is stored. Never use it for a one-off request or "
        "for sensitive personal facts."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "slot": {"type": "string", "enum": [s.key for s in SlotRegistry.load().all()]},
            "value": {
                "description": "The value for the slot, within the slot's allowed values.",
                "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
            },
            "quote": {
                "type": "string",
                "description": "The learner's own words that state the instruction.",
            },
        },
        "required": ["slot", "value", "quote"],
    },
}


def handle_propose_memory_update(
    store: ProfileStore,
    registry: SlotRegistry,
    user_id: str,
    slot: str,
    value: object,
    quote: str,
    now: datetime,
) -> Proposal | None:
    """Create a pending proposal, or None when it is invalid, already stored, or a duplicate."""
    normalized: Value | None = registry.validate(slot, value)
    if normalized is None or not str(quote).strip():
        return None
    current = store.get_profile(user_id).get(slot)
    if current and current.status == "stated" and current.value == normalized:
        return None
    for pending in store.proposals(user_id, "pending"):
        if pending.slot_key == slot and pending.value == normalized:
            return pending
    return store.add_proposal(user_id, slot, normalized, str(quote)[:300], now)


class HybridStrategy(NightlyStrategy):
    name = "hybrid"

    def on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        if reason := self.turn_skip_reason(turn):
            return TurnResult(skipped_reason=reason)
        self.log_observation(turn)
        result = TurnResult()
        if self.set_language_now(turn, now):
            result.updated_slots.append("preferred_language")

        instruction = find_standing_instruction(truncate_prompt(turn.user_message))
        if instruction is None:
            return result
        system = f"{INSTRUCTION_SYSTEM}\n\nSlots:\n{slot_definitions(self.registry, include_deterministic=True)}"
        reply = self.call_model("turn", turn.user_id, system, instruction)
        result.llm_calls = 1
        mapped = parse_json_object(reply.text) or {}
        proposal = handle_propose_memory_update(
            self.store, self.registry, turn.user_id, str(mapped.get("slot")), mapped.get("value"),
            instruction, now,
        )  # fmt: skip
        if proposal is None:
            result.skipped_reason = "instruction_needs_settings"
        else:
            result.proposals.append(proposal)
        return result

    def set_language_now(self, turn: Turn, now: datetime) -> bool:
        """preferred_language from every session observed so far, in code."""
        sessions: dict[str, list[str]] = {}
        for obs in self.store.observations(turn.user_id, since=now - timedelta(days=WINDOW_DAYS)):
            sessions.setdefault(obs.session_id, []).append(obs.text)
        language = profile_language([session_language(texts) for texts in sessions.values()])
        message = truncate_prompt(turn.user_message)
        proof = verbatim_evidence(
            message.strip()[:80], message, turn.session_id, turn.message_id, now
        )
        return bool(
            language
            and self.apply_update(
                turn.user_id, "preferred_language", language, [proof] if proof else [], now
            )
        )

    def blocked_slots(self, user_id: str) -> set[str]:
        return {p.slot_key for p in self.store.proposals(user_id, "pending")}
