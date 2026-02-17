"""Shared helpers for V2 chat services."""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import anthropic
import braintrust

T = TypeVar("T")


def get_anthropic_client(
    api_key: str | None = None, base_url: str | None = None
) -> anthropic.Anthropic:
    """Create an Anthropic client, reading the key from env if not provided.

    Raises ValueError if no key is available.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY is required")
    kwargs: dict[str, Any] = {"api_key": key}
    if base_url:
        kwargs["base_url"] = base_url
    return anthropic.Anthropic(**kwargs)


@dataclass
class BraintrustConfig:
    api_key: str
    project: str
    enabled: bool


def get_braintrust_config() -> BraintrustConfig:
    """Read Braintrust config from environment.

    ``BRAINTRUST_LOGGING_ENABLED`` defaults to ``true``.  Set to ``false`` to
    disable Braintrust tracing/logging (useful during load tests).
    Prompt fetching is unaffected and always runs when BRAINTRUST_API_KEY is set.
    """
    return BraintrustConfig(
        api_key=os.environ.get("BRAINTRUST_API_KEY", ""),
        project=os.environ.get("BRAINTRUST_PROJECT", "On Site Agent"),
        enabled=os.environ.get("BRAINTRUST_LOGGING_ENABLED", "true").lower() == "true",
    )


def flush_braintrust() -> None:
    """Flush pending Braintrust spans so they're sent before the request ends."""
    braintrust.flush()


def strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ``` or ``` ... ```) from text."""
    return re.sub(r"```(?:\w+)?\n?(.*?)```", r"\1", text, flags=re.DOTALL).strip()


def make_singleton(factory: Callable[[], T]) -> tuple[Callable[[], T], Callable[[], None]]:
    """Create a (get, reset) pair for a lazy singleton.

    Usage:
        get_my_service, reset_my_service = make_singleton(MyService)
    """
    state: dict[str, T | None] = {"instance": None}

    def get() -> T:
        if state["instance"] is None:
            state["instance"] = factory()
        return state["instance"]

    def reset() -> None:
        state["instance"] = None

    return get, reset
