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
        braintrust_logging_enabled: bool = True,
        thinking_disabled: bool = True,
        session_cwd: str | None = None,
        auto_compact_window: str = "",
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
        self.session_cwd = session_cwd
        self.auto_compact_window = auto_compact_window
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
        session_store: Any = None,
        resume: str | None = None,
    ) -> tuple[Any, bool]:
        """Construct options and return (options, system_prompt_in_options).

        With ``session_store``, the SDK mirrors the transcript to the store and
        ``resume`` continues that stored session.
        """
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
            }
            if self.braintrust_logging_enabled:
                env["BRAINTRUST_API_KEY"] = self.braintrust_api_key
                env["BRAINTRUST_PROJECT"] = self.braintrust_project
            if session_store is not None and self.auto_compact_window:
                # Resumed sessions grow every turn; the CLI auto-compacts at this window.
                env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = self.auto_compact_window
            options_kwargs["env"] = env
        if debug_enabled:
            if self._supports_option("extra_args"):
                options_kwargs["extra_args"] = {"debug-to-stderr": None}
            if self._supports_option("stderr"):
                options_kwargs["stderr"] = lambda line: self.logger.warning("Claude CLI: %s", line)

        if self.thinking_disabled and self._supports_option("thinking"):
            options_kwargs["thinking"] = {"type": "disabled"}

        if session_store is not None:
            options_kwargs["session_store"] = session_store
            options_kwargs["resume"] = resume
            if self.session_cwd:
                # The store's project_key derives from cwd, so pin it across pods/deploys.
                options_kwargs["cwd"] = self.session_cwd

        system_prompt_in_options = False
        if self._supports_option("system_prompt"):
            options_kwargs["system_prompt"] = system_prompt
            if session_store is not None:
                # Without snapshot=False a resumed session keeps its first-turn
                # system prompt and ignores the router's per-turn core prompt.
                options_kwargs["system_prompt"] = {
                    "type": "custom",
                    "prompt": system_prompt,
                    "snapshot": False,
                }
            system_prompt_in_options = True

        return self.options_cls(**options_kwargs), system_prompt_in_options
