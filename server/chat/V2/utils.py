"""Shared helpers for V2 chat services."""

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

import anthropic
import braintrust

T = TypeVar("T")


def get_anthropic_client(api_key: str | None = None) -> anthropic.Anthropic:
    """Create an Anthropic client, reading the key from env if not provided.

    Raises ValueError if no key is available.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY is required")
    return anthropic.Anthropic(api_key=key)


@dataclass
class BraintrustConfig:
    api_key: str
    project: str


def get_braintrust_config() -> BraintrustConfig:
    """Read Braintrust config from environment."""
    return BraintrustConfig(
        api_key=os.environ.get("BRAINTRUST_API_KEY", ""),
        project=os.environ.get("BRAINTRUST_PROJECT", "On Site Agent"),
    )


def flush_braintrust() -> None:
    """Flush pending Braintrust spans so they're sent before the request ends."""
    braintrust.flush()


def strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ``` or ``` ... ```) from text."""
    return re.sub(r"```(?:\w+)?\n?(.*?)```", r"\1", text, flags=re.DOTALL).strip()


def _override_for(factory: Callable[[], T]) -> Callable[[], T] | None:
    """Return a replacement factory a host has registered for this service.

    Hosts that cannot run a service themselves name a replacement in
    ``CHAT_SERVICE_OVERRIDES``, keyed by class name. The local runner uses this
    for the three services that need Braintrust: it proxies them to our server
    rather than holding the key. Absent the setting, nothing changes.
    """
    from django.conf import settings

    overrides = getattr(settings, "CHAT_SERVICE_OVERRIDES", None) or {}
    path = overrides.get(getattr(factory, "__name__", ""))
    if not path:
        return None

    from django.utils.module_loading import import_string

    return import_string(path)


def make_singleton(factory: Callable[[], T]) -> tuple[Callable[[], T], Callable[[], None]]:
    """Create a (get, reset) pair for a lazy singleton.

    Usage:
        get_my_service, reset_my_service = make_singleton(MyService)
    """
    state: dict[str, T | None] = {"instance": None}

    def get() -> T:
        if state["instance"] is None:
            state["instance"] = (_override_for(factory) or factory)()
        return state["instance"]

    def reset() -> None:
        state["instance"] = None

    return get, reset
