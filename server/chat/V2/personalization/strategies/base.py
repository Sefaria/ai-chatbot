"""Shared contract and rules for the four memory strategies.

The privacy and safety rules live here, in code, not in prompts: sensitive turns never reach a
model or the store, stated slots are never overwritten, model output is validated, evidence
quotes must be verbatim, and prompts are truncated before they are sent.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..jev import JevClient
from ..llm import LLMClient, LLMResult, UsageLedger
from ..parsing import locate_quote, parse_json_object
from ..registry import SlotRegistry
from ..signals import find_standing_instruction, is_sensitive
from ..store import ProfileStore
from ..types import Evidence, Proposal, Turn

MAX_PROMPT_CHARS = 1000


@dataclass
class TurnResult:
    updated_slots: list[str] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    llm_calls: int = 0
    skipped_reason: str | None = None
    errors: int = 0  # memory-side failures swallowed so the chat turn never fails


@dataclass
class BatchResult:
    users_processed: int = 0
    updated_slots: list[tuple[str, str]] = field(default_factory=list)  # (user_id, slot_key)
    llm_calls: int = 0
    errors: int = 0


def truncate_prompt(text: str) -> str:
    return text[:MAX_PROMPT_CHARS]


class MemoryStrategy:
    name = "base"

    def __init__(
        self,
        store: ProfileStore,
        registry: SlotRegistry,
        llm: LLMClient,
        ledger: UsageLedger,
        model: str,
    ):
        self.store = store
        self.registry = registry
        self.llm = llm
        self.ledger = ledger
        self.model = model
        # Why updates were dropped (and how many were applied), by reason.
        self.diagnostics: Counter = Counter()

    def on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        raise NotImplementedError

    def run_batch(self, now: datetime, user_ids: list[str] | None = None) -> BatchResult:
        raise NotImplementedError

    # -- shared rules -------------------------------------------------------------------

    def turn_skip_reason(self, turn: Turn) -> str | None:
        """Why this turn must not be stored or sent to a model, or None."""
        if is_sensitive(turn.user_message):
            return "sensitive"
        if not self.store.get_settings(turn.user_id).store_enabled:
            return "store_disabled"
        return None

    def call_model(self, phase: str, user_id: str, system: str, user: str) -> LLMResult:
        result = self.llm.complete(system=system, user=user, model=self.model)
        self.ledger.record(self.name, phase, user_id, self.model, result)
        return result

    def parse_reply(self, text: str) -> dict | None:
        """Parse a model reply to a JSON object, counting repairs and failures."""
        parsed = parse_json_object(text, self.diagnostics)
        if parsed is None:
            self.diagnostics["reply_unparsable"] += 1
        return parsed

    def model_evidence(
        self, quote: object, text: str, session_id: str, message_id: str, at: datetime
    ) -> Evidence | None:
        """Evidence for a model-supplied quote; a quote that is not in `text` is counted."""
        proof = verbatim_evidence(quote, text, session_id, message_id, at)
        if proof is None:
            self.diagnostics["quote_not_verbatim"] += 1
        return proof

    def note_refusal(self, user_id: str, expected_generation: int | None, default: str) -> None:
        """Count why the store refused a write: a stale generation, withdrawn consent, or `default`."""
        self.diagnostics[self.store.refusal_reason(user_id, expected_generation) or default] += 1

    def apply_update(
        self,
        user_id: str,
        slot_key: str,
        value: object,
        evidence: list[Evidence],
        now: datetime,
        *,
        require_evidence: bool = True,
        expected_generation: int | None = None,
    ) -> bool:
        """Validate and write an inferred value. True only when the profile changed.

        Reconfirming the same inferred value with valid new evidence refreshes its evidence and
        shelf life but counts as `unchanged`.
        """
        slot = self.registry.get(slot_key)
        normalized = self.registry.validate(slot_key, value) if slot else None
        if normalized is None:
            self.diagnostics["value_invalid"] += 1
            return False
        if require_evidence and not evidence:
            self.diagnostics["no_valid_evidence"] += 1
            return False
        current = self.store.get_profile(user_id).get(slot_key)
        expires_at = now + timedelta(days=slot.shelf_life_days) if slot.shelf_life_days else None
        if current and current.value == normalized:
            self.diagnostics["unchanged"] += 1
            # A standing instruction may be one the learner asked us never to keep (for example
            # "never show me X"): its words are not refreshed into evidence.
            quotes = [e.quote for e in evidence]
            if (
                current.status == "inferred"
                and evidence
                and not any(find_standing_instruction(q) for q in quotes)
            ):
                if not self.store.set_inferred(
                    user_id, slot_key, normalized, evidence, now, expires_at, expected_generation
                ):
                    self.note_refusal(user_id, expected_generation, "stated_locked")
            return False
        if not self.store.set_inferred(
            user_id, slot_key, normalized, evidence, now, expires_at, expected_generation
        ):
            self.note_refusal(user_id, expected_generation, "stated_locked")
            return False
        self.diagnostics["applied"] += 1
        return True


def verbatim_evidence(
    quote: object, text: str, session_id: str, message_id: str, at: datetime
) -> Evidence | None:
    """Evidence only when `quote` occurs in a message the strategy saw.

    The match ignores Unicode form, whitespace runs and Hebrew or curly quote marks; the stored
    quote is the learner's own substring.
    """
    if not isinstance(quote, str):
        return None
    found = locate_quote(quote, text)
    return Evidence(found, session_id, message_id, at) if found else None


def build_strategy(
    name: str,
    store: ProfileStore,
    registry: SlotRegistry,
    llm: LLMClient,
    ledger: UsageLedger,
    model: str,
    jev: JevClient | None = None,
) -> MemoryStrategy:
    from .explicit import ExplicitStrategy
    from .hybrid import HybridStrategy
    from .hybrid_jev import HybridJevStrategy
    from .nightly import NightlyStrategy
    from .realtime import RealtimeStrategy

    classes = {
        c.name: c
        for c in (
            RealtimeStrategy,
            NightlyStrategy,
            HybridStrategy,
            HybridJevStrategy,
            ExplicitStrategy,
        )
    }
    if name not in classes:
        raise ValueError(f"unknown strategy {name!r}; expected one of {sorted(classes)}")
    if name == "hybrid_jev":
        if jev is None:
            raise ValueError("the hybrid_jev strategy needs a Jev client (jev=...)")
        return HybridJevStrategy(store, registry, llm, ledger, model, jev)
    return classes[name](store, registry, llm, ledger, model)
