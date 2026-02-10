"""Base class for services that need an Anthropic client and prompt service."""

import os

import anthropic

from .prompts import PromptService, get_prompt_service


class BaseLLMService:
    """Shared init for LLM-backed services (API key, client, prompt service).

    Provides Anthropic client, Braintrust config, and prompt service.
    """

    def __init__(
        self,
        api_key: str | None = None,
        prompt_service: PromptService | None = None,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else None
        self.prompt_service = prompt_service or get_prompt_service()
        self.braintrust_api_key = os.environ.get("BRAINTRUST_API_KEY", "")
        self.braintrust_project = os.environ.get("BRAINTRUST_PROJECT", "On Site Agent")

    def _ensure_client(self) -> None:
        """Raise ValueError if no Anthropic client is available."""
        if not self.client:
            raise ValueError("ANTHROPIC_API_KEY is required")
