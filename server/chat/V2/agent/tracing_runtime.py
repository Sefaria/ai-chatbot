"""Braintrust tracing runtime setup helpers."""

from __future__ import annotations

from collections.abc import Callable

import braintrust


def ensure_braintrust_tracing(
    *,
    project: str,
    api_key: str,
    setup_done: bool,
    setup_fn: Callable[..., None],
) -> bool:
    """Ensure Braintrust tracing is active for this thread/process."""
    from django.conf import settings

    if not settings.BRAINTRUST_LOGGING_ENABLED:
        return setup_done

    if not setup_done:
        setup_fn(project=project, api_key=api_key)
        return True

    if not braintrust.current_logger():
        braintrust.init_logger(project=project, api_key=api_key)

    return setup_done
