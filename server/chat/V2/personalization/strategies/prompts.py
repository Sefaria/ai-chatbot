"""Prompts for the memory strategies. Rules the code also enforces are repeated here for the model."""

from __future__ import annotations

import json

from ..registry import SlotRegistry
from ..types import SlotDef, SlotValue

_COMMON_RULES = """\
Output JSON only, with no prose and no markdown fences.
Use only the slots listed below and, for enum and list slots, only their allowed values.
Leave a slot out rather than guess.
For text slots, describe the answer the learner wants, never the learner. Write in the second \
person ("Keep answers short and lead with the source.").
Never record sensitive personal facts: mourning, illness, conversion, personal status, or the \
names of children or family members. If a message touches them, ignore it.
Every update needs a quote copied verbatim from a learner message. Never paraphrase a quote."""

REALTIME_SYSTEM = f"""\
You keep a short profile of a learner using a Jewish text library assistant. You see one chat \
turn and the current profile, and you return only the profile updates this turn supports.

{_COMMON_RULES}

Return {{"updates": [{{"slot": "<slot key>", "value": <value>, "quote": "<verbatim learner words>"}}]}}.
Return {{"updates": []}} when the turn shows nothing new."""

CONSOLIDATION_SYSTEM = f"""\
You consolidate what a learner's recent chats say about them into a short profile for a Jewish \
text library assistant. You see the slot definitions, the current profile, and the learner's \
prompts grouped by session, each with a date and a message id.

{_COMMON_RULES}
Do not infer from a single odd prompt. Each slot lists how many distinct sessions of evidence it \
needs; only update a slot when that many sessions support it. A slot the profile marks as stated \
was set by the learner; do not touch it.
Add a slot to "clear" only when the recent prompts clearly contradict an inferred value.

Return {{"updates": [{{"slot": "<slot key>", "value": <value>, "evidence": [{{"message_id": "<id>", \
"quote": "<verbatim learner words from that message>"}}]}}], "clear": ["<slot key>"]}}.
Return {{"updates": [], "clear": []}} when nothing changes."""

INSTRUCTION_SYSTEM = f"""\
A learner of a Jewish text library assistant wrote a standing instruction. Map it to exactly one \
of the profile slots below, or say it fits none.

{_COMMON_RULES}

Return {{"slot": "<slot key>", "value": <value>}} when it fits a slot, otherwise {{"slot": null}}."""


def slot_definitions(registry: SlotRegistry, *, include_deterministic: bool = False) -> str:
    """The slot catalogue sent to a model (deterministic slots are computed in code)."""
    lines = []
    for slot in registry.all():
        if slot.deterministic and not include_deterministic:
            continue
        lines.append(_describe(slot))
    return "\n".join(lines)


def _describe(slot: SlotDef) -> str:
    parts = [f"- {slot.key} ({slot.value_type}): {slot.instruction}"]
    if slot.allowed:
        parts.append(f"Allowed values: {', '.join(slot.allowed)}.")
    if slot.max_items:
        parts.append(f"At most {slot.max_items} items.")
    if slot.max_chars:
        parts.append(f"At most {slot.max_chars} characters.")
    parts.append(f"Needs evidence from {slot.min_sessions} session(s).")
    return " ".join(parts)


def current_profile_text(profile: dict[str, SlotValue]) -> str:
    rows = {k: {"value": v.value, "status": v.status} for k, v in profile.items()}
    return json.dumps(rows, ensure_ascii=False) if rows else "{} (empty)"
