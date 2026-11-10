"""C. hybrid: deterministic slots at once, standing instructions as proposals, batch for the rest.

A standing instruction ("from now on ...") never becomes a silent write: it becomes a pending
proposal the learner confirms into a stated slot. The batch is the nightly consolidation, minus
slots that have a pending proposal.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from typing import Any

from ..registry import SlotRegistry
from ..signals import find_standing_instruction
from ..store import ProfileStore
from ..types import Proposal, Turn, Value
from .base import TurnResult, truncate_prompt
from .nightly import NightlyStrategy
from .prompts import INSTRUCTION_SYSTEM, slot_definitions

logger = logging.getLogger(__name__)

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
    expected_generation: int | None = None,
    diagnostics: Counter | None = None,
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
    proposal = store.add_proposal(
        user_id, slot, normalized, str(quote)[:300], now, expected_generation
    )
    if proposal is None and diagnostics is not None:
        diagnostics[store.refusal_reason(user_id, expected_generation) or "proposal_refused"] += 1
    return proposal


class HybridStrategy(NightlyStrategy):
    name = "hybrid"

    def on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        if reason := self.turn_skip_reason(turn):
            return TurnResult(skipped_reason=reason)
        self.store.expire_user(turn.user_id, now)
        generation = self.store.generation(turn.user_id)
        result = TurnResult()
        instruction = find_standing_instruction(truncate_prompt(turn.user_message))
        proposal = None
        if instruction is not None:
            result.llm_calls = 1
            try:
                mapped = self.map_instruction(turn, instruction)
            except Exception:
                logger.exception("instruction classification failed")
                mapped = None
            if mapped is None:
                self.diagnostics["instruction_unclassified"] += 1
                result.errors += 1
            if mapped is None or mapped.get("excludes_sources") is True:
                # Never stored: a content-free marker only, so the prompt stays out of every later
                # window. An instruction that could not be classified is held back the same way.
                self.log_observation(turn, excluded=True, expected_generation=generation)
                result.skipped_reason = (
                    "instruction_unclassified" if mapped is None else "instruction_excludes_sources"
                )
                return result
            proposal = handle_propose_memory_update(
                self.store, self.registry, turn.user_id, str(mapped.get("slot")),
                mapped.get("value"), instruction, now, generation, self.diagnostics,
            )  # fmt: skip
        if self.log_observation(turn, expected_generation=generation) and self.update_language(
            turn.user_id, self.window_observations(turn.user_id, now), now, generation
        ):
            result.updated_slots.append("preferred_language")
        if instruction is not None:
            if proposal is None:
                result.skipped_reason = "instruction_needs_settings"
            else:
                result.proposals.append(proposal)
        return result

    def map_instruction(self, turn: Turn, instruction: str) -> dict | None:
        """The model's mapping, or None when the reply cannot be parsed."""
        system = (
            f"{INSTRUCTION_SYSTEM}\n\nSlots:\n"
            f"{slot_definitions(self.registry, include_deterministic=True)}"
        )
        reply = self.call_model("turn", turn.user_id, system, instruction)
        return self.parse_reply(reply.text)

    def blocked_slots(self, user_id: str) -> set[str]:
        return {p.slot_key for p in self.store.proposals(user_id, "pending")}
