"""Progress update helpers for safe callback emission."""

from __future__ import annotations

import logging

from .contracts import AgentProgressUpdate, ProgressCallback


class ProgressEmitter:
    """Safely emits progress updates without breaking the agent turn."""

    def __init__(
        self,
        on_progress: ProgressCallback | None,
        *,
        logger: logging.Logger | None = None,
    ):
        self._on_progress = on_progress
        self._logger = logger or logging.getLogger("chat.agent")

    def emit(self, update: AgentProgressUpdate) -> None:
        if not self._on_progress:
            return
        try:
            self._on_progress(update)
        except Exception as exc:
            self._logger.warning(f"Progress callback error: {exc}")

