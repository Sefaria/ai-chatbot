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

from ..parsing import validate_envelope
from ..registry import SlotRegistry
from ..signals import find_standing_instructions
from ..store import ProfileStore
from ..types import InstructionVerdict, Proposal, Turn, Value, WriteResult
from .base import TurnResult, classify_turn, is_clean, truncate_prompt
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
    source_message_id: str = "",
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
        return store.add_proposal(
            user_id, slot, normalized, str(quote)[:300], now, expected, source_message_id
        )

    result = submit(add) if submit is not None else add(expected_revision)
    return result.proposal


class HybridStrategy(NightlyStrategy):
    name = "hybrid"

    def _on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        if reason := self.turn_skip_reason(turn):
            return TurnResult(skipped_reason=reason)
        self.store.expire_user(turn.user_id, now)
        instructions = find_standing_instructions(turn.user_message)
        # A turn holding a standing instruction is quarantined from the start; it keeps its text
        # only once every instruction in it has been classified as a safe style preference.
        if reason := self.start_turn(turn, now, quarantined=bool(instructions)):
            return TurnResult(skipped_reason=reason)
        result = TurnResult()
        mapped: dict[str, tuple[str, object]] = {}
        if instructions:
            verdicts = classify_turn(
                instructions, lambda items: self.classify_instructions(turn, items, mapped, result)
            )
            if not is_clean(verdicts):
                if InstructionVerdict.UNKNOWN in verdicts:
                    self.diagnostics["instruction_unclassified"] += 1
                result.skipped_reason = (
                    "instruction_excludes_sources"
                    if InstructionVerdict.EXCLUSION in verdicts
                    else "instruction_unclassified"
                )
                return result
            if not self.keep_turn(turn).applied:
                result.skipped_reason = "observation_not_kept"
                return result
            for instruction, (slot, value) in mapped.items():
                proposal = handle_propose_memory_update(
                    self.store, self.registry, turn.user_id, slot, value, instruction, now,
                    source_message_id=turn.message_id,
                    submit=lambda call: self.write(turn.user_id, call),
                )  # fmt: skip
                if proposal is not None:
                    result.proposals.append(proposal)
        if self.update_language(turn.user_id, self.window_observations(turn.user_id, now), now):
            result.updated_slots.append("preferred_language")
        if instructions and not result.proposals:
            result.skipped_reason = "instruction_needs_settings"
        return result

    def classify_instructions(
        self,
        turn: Turn,
        instructions: list[str],
        mapped: dict[str, tuple[str, object]],
        result: TurnResult,
    ) -> list[InstructionVerdict]:
        """One model call per instruction. A reply that is not exactly the expected shape, or a
        failed call, leaves that instruction `UNKNOWN`. Mappings to a slot go into `mapped`."""
        verdicts = []
        for instruction in instructions:
            result.llm_calls += 1
            try:
                reply = self.map_instruction(turn, instruction)
            except Exception:
                logger.exception("instruction classification failed")
                reply = None
            if isinstance(reply, dict) and reply.get("slot") is None:
                reply = {k: v for k, v in reply.items() if k not in ("slot", "value")}
            valid = validate_envelope(
                reply, {"excludes_sources": bool}, {"slot": str, "value": object}
            )
            if valid is None:
                result.errors += 1
                verdicts.append(InstructionVerdict.UNKNOWN)
            elif valid["excludes_sources"]:
                verdicts.append(InstructionVerdict.EXCLUSION)
            else:
                verdicts.append(InstructionVerdict.SAFE_STYLE)
                if "slot" in valid:
                    mapped[instruction] = (valid["slot"], valid.get("value"))
        return verdicts

    def map_instruction(self, turn: Turn, instruction: str) -> dict | None:
        """The model's reply for one instruction, or None when it cannot be parsed."""
        system = (
            f"{INSTRUCTION_SYSTEM}\n\nSlots:\n"
            f"{slot_definitions(self.registry, include_deterministic=True)}"
        )
        reply = self.call_model("turn", turn.user_id, system, truncate_prompt(instruction))
        return self.parse_reply(reply.text)

    def blocked_slots(self, user_id: str) -> set[str]:
        return {p.slot_key for p in self.store.proposals(user_id, "pending")}
