"""Claude Agent SDK options builder."""

from __future__ import annotations

import logging
import os
from typing import Any


class SDKOptionsBuilder:
    """Builds ClaudeAgentOptions for the Sefaria agent."""

    def __init__(
        self,
        *,
        options_cls: type,
        model: str,
        max_tokens: int,
        braintrust_api_key: str,
        braintrust_project: str,
        mcp_server_name: str,
        braintrust_logging_enabled: bool = True,
        logger: logging.Logger | None = None,
    ):
        self.options_cls = options_cls
        self.model = model
        self.max_tokens = max_tokens
        self.braintrust_api_key = braintrust_api_key
        self.braintrust_project = braintrust_project
        self.braintrust_logging_enabled = braintrust_logging_enabled
        self.mcp_server_name = mcp_server_name
        self.logger = logger or logging.getLogger("chat.agent")

    def build(
        self,
        *,
        system_prompt: str,
        mcp_server: Any,
        allowed_tools: list[str],
    ) -> tuple[Any, bool]:
        """Construct options and return (options, system_prompt_in_options)."""
        env: dict[str, str] = {
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
        }
        if self.braintrust_logging_enabled:
            env["BRAINTRUST_API_KEY"] = self.braintrust_api_key
            env["BRAINTRUST_PROJECT"] = self.braintrust_project

        debug_enabled = os.environ.get("CLAUDE_SDK_DEBUG", 1)

        options = self.options_cls(
            model=self.model,
            permission_mode="bypassPermissions",
            mcp_servers={self.mcp_server_name: mcp_server},
            allowed_tools=allowed_tools,
            max_tokens=self.max_tokens,
            temperature=0,
            continue_conversation=False,
            system_prompt=system_prompt,
            env=env,
            **(
                {
                    "extra_args": {"debug-to-stderr": None},
                    "stderr": lambda line: self.logger.warning("Claude CLI: %s", line),
                }
                if debug_enabled
                else {}
            ),
        )

        return options, True
