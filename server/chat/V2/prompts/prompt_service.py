"""
Prompt service — fetches the core system prompt from Braintrust.

The core prompt (the large system-level instruction that tells Claude how to
behave as a Sefaria learning assistant) lives in Braintrust's prompt registry,
not in this codebase. This service fetches it by slug + version, caches it
in-memory with a TTL, and returns a CorePrompt dataclass.

Flow:
    ClaudeAgentService.__init__ → get_prompt_service() → PromptService
    ClaudeAgentService._send_message_inner → prompt_service.get_core_prompt()
        → cache hit? return cached
        → cache miss? fetch from Braintrust API → cache → return
"""

import logging
import os
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any

from django.conf import settings

logger = logging.getLogger("chat.prompts")


@dataclass
class CorePrompt:
    """Core prompt text with versioning metadata."""

    text: str
    prompt_id: str
    version: str


class PromptService:
    """
    Service for fetching and caching the core prompt from Braintrust.

    Features:
    - Braintrust prompt registry integration (required)
    - In-memory caching with TTL
    - Version tracking for reproducibility
    """

    def __init__(
        self,
        api_key: str | None = None,
        project_name: str | None = None,
        cache_ttl_seconds: int = 300,
    ):
        """
        Initialize the prompt service.

        Args:
            api_key: Braintrust API key (default: from env)
            project_name: Braintrust project name (default: from env)
            cache_ttl_seconds: How long to cache prompts
        """
        import braintrust

        self.api_key = api_key or os.environ.get("BRAINTRUST_API_KEY")
        if not self.api_key:
            raise RuntimeError("BRAINTRUST_API_KEY environment variable is required")
        self.project_name = project_name or os.environ.get("BRAINTRUST_PROJECT", "On Site Agent")
        self.cache_ttl = cache_ttl_seconds

        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_lock = Lock()
        self._braintrust_client = braintrust

    def get_core_prompt(
        self,
        prompt_id: str | None = None,
        version: str = "stable",
    ) -> CorePrompt:
        """
        Get the core system prompt.

        Args:
            prompt_id: Braintrust slug for core prompt (default: settings.CORE_PROMPT_SLUG)
            version: Prompt version to fetch

        Returns:
            CorePrompt with text and metadata
        """
        prompt_id = prompt_id or settings.CORE_PROMPT_SLUG
        prompt_text, actual_version = self._get_prompt(prompt_id, version)
        return CorePrompt(text=prompt_text, prompt_id=prompt_id, version=actual_version)

    def _get_prompt(self, prompt_id: str, version: str) -> tuple[str, str]:
        """
        Get a prompt by ID with caching.

        Returns (prompt_text, version_used)
        """
        cache_key = f"{prompt_id}:{version}"

        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and time.time() - cached["timestamp"] < self.cache_ttl:
                logger.debug("Prompt cache hit: %s (version=%s)", prompt_id, cached["version"])
                return cached["prompt"], cached["version"]

        try:
            fetch_start = time.time()
            prompt_text, actual_version = self._fetch_from_braintrust(prompt_id, version)
            fetch_ms = int((time.time() - fetch_start) * 1000)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to fetch prompt '{prompt_id}' from Braintrust: {exc}"
            ) from exc

        if not prompt_text:
            raise RuntimeError(
                f"Prompt '{prompt_id}' returned empty from Braintrust ({fetch_ms}ms)"
            )

        with self._cache_lock:
            self._cache[cache_key] = {
                "prompt": prompt_text,
                "version": actual_version,
                "timestamp": time.time(),
            }
        logger.info(
            "Prompt fetched from Braintrust: %s (version=%s, %dms)",
            prompt_id,
            actual_version,
            fetch_ms,
        )
        return prompt_text, actual_version

    def _fetch_from_braintrust(self, prompt_id: str, version: str) -> tuple[str | None, str]:
        """
        Fetch a prompt from Braintrust.

        Returns (prompt_text, version) or (None, "") if not found.
        Raises on network/API errors (caller handles).
        """
        prompt = self._braintrust_client.load_prompt(
            project=self.project_name,
            slug=prompt_id,
            version=version if version != "stable" else None,
        )

        if prompt is None:
            return None, ""

        actual_version = getattr(prompt, "version", version) or version
        prompt_text = self._extract_prompt_text(prompt)

        if prompt_text:
            return prompt_text, str(actual_version)

        return None, ""

    def _extract_prompt_text(self, prompt) -> str | None:
        """Extract prompt text from a Braintrust Prompt object.

        Braintrust prompts can be structured in several ways depending on the
        SDK version and how the prompt was created in the UI:

        Path 1: prompt.build() → dict with "messages" (chat format)
            → look for role="system" message → return its content
        Path 2: prompt.build() → dict with "prompt" key (completion format)
        Path 3: prompt.prompt_data.prompt.messages (older SDK object model)
        Path 4: direct attributes (prompt.prompt, .content, .text, .system)

        We try each path in order and return the first successful extraction.
        """
        try:
            if hasattr(prompt, "build"):
                built = prompt.build()
                if isinstance(built, dict):
                    messages = built.get("messages", [])
                    if messages:
                        for msg in messages:
                            if isinstance(msg, dict) and msg.get("role") == "system":
                                content = msg.get("content", "")
                                if isinstance(content, str):
                                    return content
                                if isinstance(content, list):
                                    return "".join(
                                        block.get("text", "")
                                        if isinstance(block, dict)
                                        else str(block)
                                        for block in content
                                    )
                        first_msg = messages[0] if messages else None
                        if first_msg and isinstance(first_msg, dict):
                            content = first_msg.get("content", "")
                            if isinstance(content, str):
                                return content

                    if "prompt" in built:
                        return built["prompt"]

            if hasattr(prompt, "prompt_data"):
                prompt_data = prompt.prompt_data
                if hasattr(prompt_data, "prompt"):
                    block = prompt_data.prompt
                    if hasattr(block, "messages"):
                        messages = block.messages
                        if messages:
                            for msg in messages:
                                if hasattr(msg, "role") and msg.role == "system":
                                    content = getattr(msg, "content", None)
                                    if isinstance(content, str):
                                        return content
                                    if isinstance(content, list):
                                        return "".join(
                                            getattr(part, "text", str(part)) for part in content
                                        )
                            first = messages[0]
                            if hasattr(first, "content"):
                                content = first.content
                                if isinstance(content, str):
                                    return content
                    elif hasattr(block, "content"):
                        return block.content

            for attr in ["prompt", "content", "text", "system"]:
                if hasattr(prompt, attr):
                    val = getattr(prompt, attr)
                    if isinstance(val, str) and val:
                        return val

            logger.warning(f"Could not extract text from prompt object: {type(prompt)}")
            return None

        except Exception as exc:
            logger.warning(f"Error extracting prompt text: {exc}")
            return None

    def invalidate_cache(self, prompt_id: str | None = None) -> None:
        """
        Invalidate cached prompts.

        Args:
            prompt_id: Specific prompt to invalidate, or None for all
        """
        with self._cache_lock:
            if prompt_id:
                keys_to_remove = [k for k in self._cache if k.startswith(f"{prompt_id}:")]
                for key in keys_to_remove:
                    del self._cache[key]
            else:
                self._cache.clear()

        logger.info(f"Prompt cache invalidated: {prompt_id or 'all'}")


_default_service = None


def get_prompt_service() -> PromptService:
    """Get or create the default prompt service."""
    global _default_service
    if _default_service is None:
        _default_service = PromptService()
    return _default_service
