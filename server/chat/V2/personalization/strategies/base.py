"""Shared contract and rules for the four memory strategies.

The privacy and safety rules live here, in code, not in prompts: sensitive turns never reach a
model or the store, stated slots are never overwritten, model output is validated, evidence
quotes must be verbatim, and prompts are truncated before they are sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..llm import LLMClient, LLMResult, UsageLedger
from ..registry import SlotRegistry
from ..signals import is_sensitive
from ..store import ProfileStore
from ..types import Evidence, Proposal, Turn

MAX_PROMPT_CHARS = 1000


@dataclass
class TurnResult:
    updated_slots: list[str] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    llm_calls: int = 0
    skipped_reason: str | None = None


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

    def apply_update(
        self,
        user_id: str,
        slot_key: str,
        value: object,
        evidence: list[Evidence],
        now: datetime,
        *,
        require_evidence: bool = True,
    ) -> bool:
        """Validate and write an inferred value. True only when the profile changed."""
        slot = self.registry.get(slot_key)
        normalized = self.registry.validate(slot_key, value) if slot else None
        if normalized is None or (require_evidence and not evidence):
            return False
        current = self.store.get_profile(user_id).get(slot_key)
        if current and current.value == normalized:
            return False
        expires_at = now + timedelta(days=slot.shelf_life_days) if slot.shelf_life_days else None
        return self.store.set_inferred(user_id, slot_key, normalized, evidence, now, expires_at)


def verbatim_evidence(
    quote: object, text: str, session_id: str, message_id: str, at: datetime
) -> Evidence | None:
    """Evidence only when `quote` is a verbatim substring of a message the strategy saw."""
    if not isinstance(quote, str) or not quote.strip() or quote not in text:
        return None
    return Evidence(quote, session_id, message_id, at)


def build_strategy(
    name: str,
    store: ProfileStore,
    registry: SlotRegistry,
    llm: LLMClient,
    ledger: UsageLedger,
    model: str,
) -> MemoryStrategy:
    from .explicit import ExplicitStrategy
    from .hybrid import HybridStrategy
    from .nightly import NightlyStrategy
    from .realtime import RealtimeStrategy

    classes = {
        c.name: c for c in (RealtimeStrategy, NightlyStrategy, HybridStrategy, ExplicitStrategy)
    }
    if name not in classes:
        raise ValueError(f"unknown strategy {name!r}; expected one of {sorted(classes)}")
    return classes[name](store, registry, llm, ledger, model)
