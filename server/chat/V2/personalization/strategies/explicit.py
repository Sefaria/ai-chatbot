"""D. explicit: the profile is only what the learner states (interview or settings)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..types import Turn
from .base import BatchResult, MemoryStrategy, TurnResult


class ExplicitStrategy(MemoryStrategy):
    name = "explicit"

    def _on_turn(self, turn: Turn, now: datetime) -> TurnResult:
        if reason := self.turn_skip_reason(turn):
            return TurnResult(skipped_reason=reason)
        # The ledger row is a content-free marker, stored consumed: it only makes retries no-ops.
        if reason := self.start_turn(turn, now, excluded=True, consumed=True):
            return TurnResult(skipped_reason=reason)
        return TurnResult(skipped_reason="explicit_only")

    def _run_batch(self, now: datetime, user_ids: list[str] | None = None) -> BatchResult:
        return BatchResult()

    def apply_interview(self, user_id: str, answers: dict[str, Any], now: datetime) -> list[str]:
        """Validate each answer and store it as stated. Returns the slots written.

        Consent is checked by the store at each write, so an opt-out mid-interview stops it."""
        written = []
        for key, answer in answers.items():
            value = self.registry.validate(key, answer)
            if value is not None and self.store.set_stated(user_id, key, value, now).applied:
                written.append(key)
        return written
