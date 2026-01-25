"""
Token usage metrics with provider-agnostic canonical format.

This module provides a unified TokenUsage dataclass that:
- Uses Anthropic-style field names as the canonical format
- Converts from different LLM provider response formats
- Exports to observability platform formats (Braintrust, etc.)

Braintrust Field Mapping:
    Braintrust normalizes token names for cost calculation. When logging
    metrics manually (without wrap_anthropic), we must use their expected names:

    Anthropic API           → Braintrust Expected
    ─────────────────────────────────────────────
    input_tokens            → prompt_tokens
    output_tokens           → completion_tokens
    cache_creation_input_tokens → prompt_cache_creation_tokens
    cache_read_input_tokens → prompt_cached_tokens
    (calculated total)      → tokens

    Source: braintrust-sdk/py/src/braintrust/wrappers/_anthropic_utils.py
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class TokenUsage:
    """Canonical token usage format (Anthropic-aligned).

    Attributes:
        input_tokens: Tokens in the input/prompt
        output_tokens: Tokens in the output/completion
        cache_creation_input_tokens: Tokens written to prompt cache
        cache_read_input_tokens: Tokens read from prompt cache
    """

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Total tokens including all cache tokens."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        """Sum token usage across multiple API calls."""
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(self.cache_read_input_tokens + other.cache_read_input_tokens),
        )

    @classmethod
    def zero(cls) -> "TokenUsage":
        """Create a zero-value TokenUsage for initialization."""
        return cls(input_tokens=0, output_tokens=0)

    @classmethod
    def from_anthropic(cls, usage: Any) -> "TokenUsage":
        """Extract TokenUsage from Anthropic API response.usage object.

        Args:
            usage: The usage object from anthropic.messages.create() response

        Returns:
            TokenUsage with values extracted from the Anthropic response
        """
        return cls(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )

    def to_braintrust(self) -> dict[str, int]:
        """Convert to Braintrust metrics format for span.log(metrics=...).

        Braintrust uses OpenAI-style naming internally for cost calculation.
        This method maps our Anthropic-style names to Braintrust's expected format.

        Returns:
            Dict with Braintrust-expected metric names:
            - prompt_tokens: input token count
            - completion_tokens: output token count
            - prompt_cache_creation_tokens: cache write tokens
            - prompt_cached_tokens: cache read tokens
            - tokens: total of all token types
        """
        return {
            "prompt_tokens": self.input_tokens,
            "completion_tokens": self.output_tokens,
            "prompt_cache_creation_tokens": self.cache_creation_input_tokens,
            "prompt_cached_tokens": self.cache_read_input_tokens,
            "tokens": self.total_tokens,
        }
