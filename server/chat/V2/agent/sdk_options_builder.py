"""Claude Agent SDK options builder with runtime feature detection."""

from __future__ import annotations

import inspect
import logging
import os
from typing import Any


class SDKOptionsBuilder:
    """Builds ClaudeAgentOptions compatible with multiple SDK versions."""

    def __init__(
        self,
        *,
        options_cls: type,
        model: str,
        max_tokens: int,
        temperature: float,
        braintrust_api_key: str,
        braintrust_project: str,
        mcp_server_name: str,
        logger: logging.Logger | None = None,
    ):
        self.options_cls = options_cls
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.braintrust_api_key = braintrust_api_key
        self.braintrust_project = braintrust_project
        self.mcp_server_name = mcp_server_name
        self.logger = logger or logging.getLogger("chat.agent")

    def _supports_option(self, option_name: str) -> bool:
        """Check whether the installed SDK constructor accepts an option."""
        try:
            signature = inspect.signature(self.options_cls)
        except (TypeError, ValueError):
            return False

        for param in signature.parameters.values():
            if param.kind == inspect.Parameter.VAR_KEYWORD:
                return True

        return option_name in signature.parameters

    def build(
        self,
        *,
        system_prompt: str,
        mcp_server: Any,
        allowed_tools: list[str],
    ) -> tuple[Any, bool]:
        """Construct options and return (options, system_prompt_in_options)."""
        options_kwargs: dict[str, Any] = {
            "model": self.model,
            "permission_mode": "bypassPermissions",
            "mcp_servers": {self.mcp_server_name: mcp_server},
            "allowed_tools": allowed_tools,
        }

        debug_enabled = os.environ.get("CLAUDE_SDK_DEBUG", 1)

        if self._supports_option("max_tokens"):
            options_kwargs["max_tokens"] = self.max_tokens
        if self._supports_option("temperature"):
            options_kwargs["temperature"] = self.temperature
        if self._supports_option("continue_conversation"):
            options_kwargs["continue_conversation"] = False
        if self._supports_option("env"):
            options_kwargs["env"] = {
                "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
                "BRAINTRUST_API_KEY": self.braintrust_api_key,
                "BRAINTRUST_PROJECT": self.braintrust_project,
            }
        if debug_enabled:
            if self._supports_option("extra_args"):
                options_kwargs["extra_args"] = {"debug-to-stderr": None}
            if self._supports_option("stderr"):
                options_kwargs["stderr"] = lambda line: self.logger.warning("Claude CLI: %s", line)

        system_prompt_in_options = False
        if self._supports_option("system_prompt"):
            options_kwargs["system_prompt"] = system_prompt
            system_prompt_in_options = True

        return self.options_cls(**options_kwargs), system_prompt_in_options
