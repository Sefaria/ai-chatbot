"""Glue between the request path and the memory strategies (not wired into views yet)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.conf import settings

from .registry import SlotRegistry
from .render import render_profile
from .signals import KeywordSensitiveScreen, SensitiveScreen, value_is_sensitive
from .store import ProfileStore
from .strategies.base import MemoryStrategy, TurnResult
from .types import Turn, Value, WriteResult


class PersonalizationService:
    def __init__(
        self,
        store: ProfileStore,
        registry: SlotRegistry,
        strategy: MemoryStrategy | None,
        mode: str | None = None,
        screen: SensitiveScreen | None = None,
    ):
        self.store = store
        self.registry = registry
        self.strategy = strategy
        self._mode = mode
        self.screen = screen or KeywordSensitiveScreen()

    @property
    def mode(self) -> str:
        return self._mode if self._mode is not None else settings.PERSONALIZATION_MODE

    def profile_context(self, user_id: str | None, now: datetime) -> tuple[str, list[str]]:
        """(profile text, slots used) for the prompt; empty when off, without a user id or opted out.

        Anonymous users (`anon:<hash>` ids) do get a profile, by design.
        """
        if self.mode == "off" or not user_id:
            return "", []
        if not self.store.get_settings(user_id).personalize_enabled:
            return "", []
        return render_profile(self.store.get_profile(user_id), self.registry, now)

    def after_turn(self, turn: Turn, now: datetime) -> TurnResult | None:
        """Feed a finished turn to the strategy. A no-op when the feature is off."""
        if self.mode == "off" or self.strategy is None or not turn.user_id:
            return None
        return self.strategy.on_turn(turn, now)

    def set_stated(self, user_id: str, slot: str, value: Value, now: datetime) -> WriteResult:
        """The one way to write a stated value from outside the store (the profile API included).

        A sensitive free-text value is refused with reason `sensitive` before the store is
        touched; the store itself still checks consent and stated-wins."""
        if value_is_sensitive(self.screen, value):
            return WriteResult(False, "sensitive", self.store.revision(user_id))
        return self.store.set_stated(user_id, slot, value, now)

    def apply_interview(self, user_id: str, answers: dict[str, Any], now: datetime) -> list[str]:
        """Validate each answer, screen it, and store it as stated. Returns the slots written."""
        written = []
        for key, answer in answers.items():
            value = self.registry.validate(key, answer)
            if value is not None and self.set_stated(user_id, key, value, now).applied:
                written.append(key)
        return written
