"""Claude Agent SDK execution loop and message text extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage


@dataclass
class SDKRunResult:
    final_text: str
    trace_id: str | None
    llm_call_count: int
    usage: dict[str, Any] | None
    total_cost_usd: float | None


class ClaudeSDKRunner:
    """Runs the query -> receive_response loop for Claude Agent SDK."""

    def __init__(
        self,
        *,
        client_cls: type = ClaudeSDKClient,
        assistant_message_cls: type = AssistantMessage,
        result_message_cls: type = ResultMessage,
    ):
        self.client_cls = client_cls
        self.assistant_message_cls = assistant_message_cls
        self.result_message_cls = result_message_cls

    async def run(self, *, options: Any, prompt_text: str) -> SDKRunResult:
        final_text = ""
        trace_id = None
        llm_call_count = 0
        usage: dict[str, Any] | None = None
        total_cost_usd: float | None = None

        async with self.client_cls(options=options) as client:
            await client.query(prompt_text)
            async for message in client.receive_response():
                if isinstance(message, self.assistant_message_cls):
                    llm_call_count += 1
                if isinstance(message, self.result_message_cls):
                    usage = message.usage
                    total_cost_usd = message.total_cost_usd
                else:
                    chunk = self.extract_text_from_message(message)
                    if chunk:
                        final_text += chunk

            trace_id = getattr(client, "trace_id", None) or getattr(client, "last_trace_id", None)

        return SDKRunResult(
            final_text=final_text,
            trace_id=trace_id,
            llm_call_count=llm_call_count,
            usage=usage,
            total_cost_usd=total_cost_usd,
        )

    @staticmethod
    def extract_text_from_message(message: Any) -> str:
        """Extract plain text from SDK response message variants."""
        if isinstance(message, str):
            return message

        if isinstance(message, dict):
            if "text" in message and isinstance(message["text"], str):
                return message["text"]
            if "content" in message:
                return ClaudeSDKRunner.extract_text_from_blocks(message["content"])

        content = getattr(message, "content", None)
        if content is not None:
            return ClaudeSDKRunner.extract_text_from_blocks(content)

        text = getattr(message, "text", None)
        if isinstance(text, str):
            return text

        return ""

    @staticmethod
    def extract_text_from_blocks(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                parts.append(ClaudeSDKRunner.extract_text_from_block(block))
            return "".join(parts)
        return ""

    @staticmethod
    def extract_text_from_block(block: Any) -> str:
        if isinstance(block, str):
            return block
        if isinstance(block, dict):
            if block.get("type") == "text":
                return block.get("text", "")
            return block.get("text", "") if "text" in block else ""

        block_type = getattr(block, "type", None)
        if block_type == "text":
            return getattr(block, "text", "") or ""
        return getattr(block, "text", "") if hasattr(block, "text") else ""

