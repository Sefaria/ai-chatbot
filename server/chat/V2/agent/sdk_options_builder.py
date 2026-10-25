"""Claude Agent SDK options builder with runtime feature detection."""

from __future__ import annotations

import inspect
import logging
import os
from typing import Any

# The Claude CLI offloads any single MCP tool result above this many tokens
# to a file (or hard-truncates it) instead of inlining it -- independent of
# how much of the model's 200K-token context window is actually free. Unset,
# it falls back to a remote experiment flag before a 25,000 default, so a
# verse with enough linked commentaries (e.g. Genesis 12:1's ~120, whose
# compressed previews run to ~26-27K tokens) can trip it even though the
# model could easily have read the whole thing in one pass -- confirmed by
# feeding it the same compressed previews directly: it found sources the
# chunked-file-read fallback the CLI substitutes in that case missed.
#
# Set explicitly (a) to stop that remote experiment from silently picking a
# lower value for some cohort of users, and (b) to buy headroom -- ~1.5x the
# worst case measured so far -- without raising it so far that a turn with
# several large tool calls (max_iterations=10) risks crowding out the rest
# of a long-running conversation's history.
MAX_MCP_OUTPUT_TOKENS = "40000"


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
        braintrust_logging_enabled: bool = True,
        thinking_disabled: bool = True,
        logger: logging.Logger | None = None,
    ):
        self.options_cls = options_cls
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.braintrust_api_key = braintrust_api_key
        self.braintrust_project = braintrust_project
        self.braintrust_logging_enabled = braintrust_logging_enabled
        self.mcp_server_name = mcp_server_name
        self.thinking_disabled = thinking_disabled
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
        if self._supports_option("include_partial_messages"):
            options_kwargs["include_partial_messages"] = True
        if self._supports_option("continue_conversation"):
            options_kwargs["continue_conversation"] = False
        if self._supports_option("env"):
            env: dict[str, str] = {
                "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
                "MAX_MCP_OUTPUT_TOKENS": MAX_MCP_OUTPUT_TOKENS,
            }
            if self.braintrust_logging_enabled:
                env["BRAINTRUST_API_KEY"] = self.braintrust_api_key
                env["BRAINTRUST_PROJECT"] = self.braintrust_project
            options_kwargs["env"] = env
        if debug_enabled:
            if self._supports_option("extra_args"):
                options_kwargs["extra_args"] = {"debug-to-stderr": None}
            if self._supports_option("stderr"):
                options_kwargs["stderr"] = lambda line: self.logger.warning("Claude CLI: %s", line)

        if self.thinking_disabled and self._supports_option("thinking"):
            options_kwargs["thinking"] = {"type": "disabled"}

        system_prompt_in_options = False
        if self._supports_option("system_prompt"):
            options_kwargs["system_prompt"] = system_prompt
            system_prompt_in_options = True

        return self.options_cls(**options_kwargs), system_prompt_in_options
