"""Tool registration/runtime bridge for Claude Agent SDK."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from .contracts import AgentProgressUpdate
from .helpers import truncate
from .tool_executor import SefariaToolExecutor, describe_tool_call


class ToolRuntime:
    """Builds SDK-compatible handlers around SefariaToolExecutor."""

    def __init__(
        self,
        *,
        tool_executor: SefariaToolExecutor,
        decorator_fn: Callable[..., Any],
    ):
        self.tool_executor = tool_executor
        self._decorate = decorator_fn

    def build_sdk_tools(
        self,
        *,
        tool_schemas: list[dict[str, Any]],
        emit: Callable[[AgentProgressUpdate], None],
        tool_calls_list: list[dict[str, Any]],
    ) -> list[Any]:
        """Create SDK-compatible tool handlers from JSON schemas."""
        sdk_tools: list[Any] = []

        def build_handler(
            tool_name: str,
            tool_description: str,
            input_schema: dict[str, Any],
        ) -> Any:
            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                tool_input = args or {}
                tool_desc = describe_tool_call(tool_name, tool_input)

                emit(
                    AgentProgressUpdate(
                        type="tool_start",
                        tool_name=tool_name,
                        tool_input=tool_input,
                        description=tool_desc,
                    )
                )

                tool_start = time.time()
                result = await self.tool_executor.execute(tool_name, tool_input)
                tool_latency = int((time.time() - tool_start) * 1000)

                output_text = "".join(
                    block.get("text", "") if block.get("type") == "text" else json.dumps(block)
                    for block in result.content
                )
                output_preview = truncate(output_text, 500)

                tool_calls_list.append(
                    {
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "tool_output": output_preview,
                        "is_error": result.is_error,
                        "latency_ms": tool_latency,
                    }
                )

                emit(
                    AgentProgressUpdate(
                        type="tool_end",
                        tool_name=tool_name,
                        is_error=result.is_error,
                        output_preview=output_preview,
                    )
                )

                return {
                    "content": result.content,
                    "is_error": result.is_error,
                }

            return self._decorate_tool(
                handler=handler,
                tool_name=tool_name,
                tool_description=tool_description,
                input_schema=input_schema,
            )

        for schema in tool_schemas:
            name = schema["name"]
            description = schema.get("description", "")
            input_schema = schema.get("input_schema", {})
            sdk_tools.append(build_handler(name, description, input_schema))

        return sdk_tools

    def _decorate_tool(
        self,
        *,
        handler: Callable[[dict[str, Any]], Any],
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
    ) -> Any:
        """Register a handler with SDK tool decorator with fallback schema."""
        try:
            return self._decorate(tool_name, tool_description, input_schema)(handler)
        except Exception:
            fallback_schema = self._simplify_schema(input_schema)
            return self._decorate(tool_name, tool_description, fallback_schema)(handler)

    @staticmethod
    def _simplify_schema(input_schema: dict[str, Any]) -> dict[str, Any]:
        """Convert JSON Schema properties to bare Python types."""
        if not isinstance(input_schema, dict):
            return input_schema

        properties = input_schema.get("properties")
        if not isinstance(properties, dict):
            return input_schema

        type_map = {
            "string": str,
            "number": float,
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        simplified: dict[str, Any] = {}
        for key, schema in properties.items():
            if isinstance(schema, dict):
                type_name = schema.get("type", "string")
                simplified[key] = (
                    type_map.get(type_name, str) if isinstance(type_name, str) else str
                )
            else:
                simplified[key] = str

        return simplified
