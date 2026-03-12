"""Sentry helpers for V2 endpoints."""

from __future__ import annotations

from typing import Any

from django.conf import settings


def capture_exception(exc: BaseException, **context: Any) -> None:
    """Capture an exception in Sentry with optional structured context."""
    if not settings.SENTRY_DSN:
        return

    import sentry_sdk

    if not context:
        sentry_sdk.capture_exception(exc)
        return

    with sentry_sdk.new_scope() as scope:
        normalized = {k: v for k, v in context.items() if v is not None}
        if normalized:
            scope.set_context("chat_v2", normalized)
        sentry_sdk.capture_exception(exc)
