"""Glue between the request path and the memory strategies (not wired into views yet)."""

from __future__ import annotations

from datetime import datetime

from django.conf import settings

from .registry import SlotRegistry
from .render import render_profile
from .store import ProfileStore
from .strategies.base import MemoryStrategy, TurnResult
from .types import Turn


class PersonalizationService:
    def __init__(
        self,
        store: ProfileStore,
        registry: SlotRegistry,
        strategy: MemoryStrategy | None,
        mode: str | None = None,
    ):
        self.store = store
        self.registry = registry
        self.strategy = strategy
        self._mode = mode

    @property
    def mode(self) -> str:
        return self._mode if self._mode is not None else settings.PERSONALIZATION_MODE

    def profile_context(self, user_id: str | None, now: datetime) -> tuple[str, list[str]]:
        """(profile text, slots used) for the prompt; empty when off, anonymous or opted out."""
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
