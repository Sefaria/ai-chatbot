"""Adapter exposing a chatbot tool schema as a FastMCP tool.

``fastmcp.tools.base.Tool`` carries ``parameters`` as a raw JSON schema, so each
``TOOL_*`` dict's ``input_schema`` is usable directly — no generated typed
signatures. Execution delegates to the same ``SefariaToolExecutor`` the agent uses.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.tools.base import Tool, ToolResult
from jsonschema import Draft202012Validator
from mcp.types import TextContent, ToolAnnotations

from chat.V2.agent.contracts import MessageContext
from chat.V2.agent.sefaria_client import SefariaClient
from chat.V2.agent.tool_executor import SefariaToolExecutor
from mcp_auth import sefaria_tokens

from . import metrics


def _text_of(content: list[dict[str, Any]]) -> str:
    """Flatten the executor's content blocks into a single string.

    ``SefariaToolExecutor`` always emits one text block (JSON-serialized for
    non-string payloads), so this is a concatenation rather than a conversion.
    """
    return "".join(
        block.get("text", "") if block.get("type") == "text" else json.dumps(block)
        for block in content
    )


class SefariaTool(Tool):
    """A single Sefaria tool, backed by the shared executor."""

    KEY_PREFIX = "tool"

    executor: Any = None
    validator: Any = None

    @classmethod
    def from_schema(cls, schema: dict[str, Any], executor: Any) -> SefariaTool:
        parameters = schema.get("input_schema", {"type": "object", "properties": {}})
        annotations = schema.get("annotations")
        return cls(
            name=schema["name"],
            description=schema.get("description", ""),
            parameters=parameters,
            annotations=ToolAnnotations(**annotations) if annotations else None,
            executor=executor,
            validator=Draft202012Validator(parameters),
        )

    def _argument_errors(self, arguments: dict[str, Any]) -> str | None:
        """Describe schema violations, or None if the arguments are valid.

        FastMCP does not validate a raw ``parameters`` schema, and the executor reads
        arguments by key, so without this a missing or mistyped argument surfaces as a
        bare Python error (``'reference'``) instead of something a caller can act on.
        """
        errors = sorted(self.validator.iter_errors(arguments), key=lambda e: list(e.path))
        if not errors:
            return None
        messages = [
            (f"{'.'.join(map(str, e.path))}: " if e.path else "") + e.message for e in errors[:3]
        ]
        return f"Invalid arguments for {self.name}: " + "; ".join(messages)

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        started = time.time()
        arguments = arguments or {}
        invalid = self._argument_errors(arguments)
        if invalid:
            self._record(started, status="error", error_type="InvalidArguments")
            raise ToolError(invalid)
        try:
            result = await self._execute(arguments)
        except Exception as exc:
            self._record(started, status="error", error_type=type(exc).__name__)
            raise ToolError(str(exc)) from exc

        text = _text_of(result.content)

        if result.is_error:
            # The executor catches its own exceptions and reports them as content,
            # so surface them as MCP tool errors rather than as successful results.
            self._record(started, status="error", error_type="ToolError")
            raise ToolError(text)

        self._record(started, status="success", payload_bytes=len(text.encode()))
        return ToolResult(content=[TextContent(type="text", text=text)])

    async def _execute(self, arguments: dict[str, Any]):
        return await self.executor.execute(self.name, arguments)

    def _record(
        self,
        started: float,
        *,
        status: str,
        error_type: str | None = None,
        payload_bytes: int | None = None,
    ) -> None:
        labels = metrics.labels
        labels(metrics.tool_duration_seconds, tool_name=self.name).observe(time.time() - started)
        labels(metrics.tool_calls_total, tool_name=self.name, status=status).inc()
        if error_type is not None:
            labels(metrics.errors_total, tool_name=self.name, error_type=error_type).inc()
        if payload_bytes is not None:
            labels(metrics.tool_payload_bytes, tool_name=self.name).observe(payload_bytes)


class UserSefariaTool(SefariaTool):
    """A tool that acts as the signed-in user on /mcp/user and /mcp/admin.

    Each call gets its own ``SefariaClient``, so concurrent users never share session
    state, and a freshly minted 5-minute ``mcp`` token for the user named in the
    verified access token. Nothing long-lived is ever sent to Sefaria.
    """

    token_secret: Any = None

    @classmethod
    def from_schema(cls, schema: dict[str, Any], executor: Any = None, *, token_secret: str):
        tool = super().from_schema(schema, executor)
        tool.token_secret = token_secret
        return tool

    async def _execute(self, arguments: dict[str, Any]):
        access_token = get_access_token()
        user_id = (access_token.claims or {}).get("sefaria_user_id") if access_token else None
        if not user_id:
            raise ToolError("Sign in with your Sefaria account to use this tool.")

        client = SefariaClient()
        executor = SefariaToolExecutor(client=client)
        executor.set_message_context(
            MessageContext(
                sefaria_user_id=str(user_id),
                encrypted_user_token=sefaria_tokens.mint(int(user_id), self.token_secret),
            )
        )
        try:
            return await executor.execute(self.name, arguments)
        finally:
            await client.close()
