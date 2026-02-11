"""Shared helpers for V2 chat services."""

import os
from dataclasses import dataclass

import anthropic
import braintrust


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
