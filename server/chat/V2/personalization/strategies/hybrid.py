"""C. hybrid: deterministic slots at once, standing instructions as proposals, batch for the rest.

A standing instruction ("from now on ...") never becomes a silent write: it becomes a pending
proposal the learner confirms into a stated slot. The batch is the nightly consolidation, minus
slots that have a pending proposal.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..registry import SlotRegistry
from ..signals import find_standing_instruction
from ..store import ProfileStore
from ..types import Proposal, Turn, Value, WriteResult
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
    expected_revision: int | None = None,
    *,
    submit: Callable[[Callable[[int | None], WriteResult]], WriteResult] | None = None,
) -> Proposal | None:
    """Create a pending proposal, or None when it is invalid, already stored, or refused.

    A duplicate is resolved by the store, inside the user's lock: the pending proposal for the
    slot is returned. `submit` runs the store call (a strategy passes its `write`, which supplies
    the expected revision and counts the outcome).
    """
    normalized: Value | None = registry.validate(slot, value)
    if normalized is None or not str(quote).strip():
        return None
    current = store.get_profile(user_id).get(slot)
    if current and current.status == "stated" and current.value == normalized:
        return None

    def add(expected: int | None) -> WriteResult:
        return store.add_proposal(user_id, slot, normalized, str(quote)[:300], now, expected)

    result = submit(add) if submit is not None else add(expected_revision)
    return result.proposal


class HybridStrategy(NightlyStrategy):
    name = "hybrid"

    def _on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        if reason := self.turn_skip_reason(turn):
            return TurnResult(skipped_reason=reason)
        self.store.expire_user(turn.user_id, now)
        instruction = find_standing_instruction(truncate_prompt(turn.user_message))
        # A turn holding a standing instruction is logged as a content-free marker: its words
        # reach the profile only through a proposal the learner confirms.
        if reason := self.start_turn(turn, now, excluded=instruction is not None):
            return TurnResult(skipped_reason=reason)
        result = TurnResult()
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
                # Never stored beyond the marker. An instruction that could not be classified is
                # held back the same way.
                result.skipped_reason = (
                    "instruction_unclassified" if mapped is None else "instruction_excludes_sources"
                )
                return result
            proposal = handle_propose_memory_update(
                self.store, self.registry, turn.user_id, str(mapped.get("slot")),
                mapped.get("value"), instruction, now,
                submit=lambda call: self.write(turn.user_id, call),
            )  # fmt: skip
        if self.update_language(turn.user_id, self.window_observations(turn.user_id, now), now):
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
