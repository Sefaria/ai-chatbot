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
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import TextContent

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

    @classmethod
    def from_schema(cls, schema: dict[str, Any], executor: Any) -> SefariaTool:
        return cls(
            name=schema["name"],
            description=schema.get("description", ""),
            parameters=schema.get("input_schema", {"type": "object", "properties": {}}),
            executor=executor,
        )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        started = time.time()
        try:
            result = await self.executor.execute(self.name, arguments or {})
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

    def _record(
        self,
        started: float,
        *,
        status: str,
        error_type: str | None = None,
        payload_bytes: int | None = None,
    ) -> None:
        metrics.tool_duration_seconds.labels(tool_name=self.name).observe(time.time() - started)
        metrics.tool_calls_total.labels(tool_name=self.name, status=status).inc()
        if error_type is not None:
            metrics.errors_total.labels(tool_name=self.name, error_type=error_type).inc()
        if payload_bytes is not None:
            metrics.tool_payload_bytes.labels(tool_name=self.name).observe(payload_bytes)
