"""
Prompt service with Braintrust integration.

Fetches the core system prompt from Braintrust with a local fallback.
Supports versioning and caching.
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
    - Braintrust prompt registry integration
    - In-memory caching with TTL
    - Fallback to local default
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
        self.api_key = api_key or os.environ.get("BRAINTRUST_API_KEY")
        self.project_name = project_name or os.environ.get("BRAINTRUST_PROJECT", "sefaria-chatbot")
        self.cache_ttl = cache_ttl_seconds

        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_lock = Lock()
        self._braintrust_client = None

        from .default_prompts import CORE_PROMPT

        self._default_core_prompt = CORE_PROMPT

        self._init_braintrust()

    def _init_braintrust(self) -> None:
        """Initialize Braintrust client if configured."""
        if not self.api_key:
            logger.info("Braintrust API key not configured, using local core prompt only")
            return

        try:
            import braintrust

            self._braintrust_client = braintrust
            logger.info(f"Braintrust client initialized for project: {self.project_name}")
        except ImportError:
            logger.warning("Braintrust package not installed, using local core prompt only")
        except Exception as exc:
            logger.warning(f"Failed to initialize Braintrust: {exc}")

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

        if self._braintrust_client:
            try:
                fetch_start = time.time()
                prompt_text, actual_version = self._fetch_from_braintrust(prompt_id, version)
                fetch_ms = int((time.time() - fetch_start) * 1000)
                if prompt_text:
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
                logger.warning("Prompt fetch returned empty: %s (%dms)", prompt_id, fetch_ms)
            except Exception as exc:
                logger.warning(f"Failed to fetch prompt {prompt_id} from Braintrust: {exc}")

        logger.info("Using local fallback prompt for: %s", prompt_id)
        return self._default_core_prompt, "local"

    def _fetch_from_braintrust(self, prompt_id: str, version: str) -> tuple[str | None, str]:
        """
        Fetch a prompt from Braintrust.

        Returns (prompt_text, version) or (None, "") if not found.
        """
        try:
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

        except Exception as exc:
            logger.warning(f"Braintrust prompt fetch error for {prompt_id}: {exc}")
            return None, ""

    def _extract_prompt_text(self, prompt) -> str | None:
        """
        Extract prompt text from a Braintrust Prompt object.

        Braintrust prompts can be:
        - Chat prompts: have messages array with system/user messages
        - Completion prompts: have a single prompt string

        Returns the system prompt text or None if extraction fails.
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
