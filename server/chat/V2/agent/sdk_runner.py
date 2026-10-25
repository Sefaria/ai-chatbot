"""Claude Agent SDK execution loop and message text extraction."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, StreamEvent, ToolUseBlock

from ..prompts.prompt_fragments import RESPOND_TOOL_NAME
from .contracts import CancelCheck, TurnCancelled

_RESPOND_TOOL_SUFFIX = f"__{RESPOND_TOOL_NAME}"
_JSON_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}


class JsonStringFieldStream:
    """Decodes one string field of a JSON object as its text streams in.

    Tool input arrives as partial JSON (`input_json_delta`); this pulls the
    field's value out piece by piece so it can be shown before the call ends.
    """

    def __init__(self, field: str):
        self._key = f'"{field}"'
        self._buffer = ""
        self._pos: int | None = None
        self._done = False

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        if self._done:
            return ""
        if self._pos is None:
            start = self._buffer.find(self._key)
            if start < 0:
                return ""
            i = start + len(self._key)
            while i < len(self._buffer) and self._buffer[i] in " \t\n\r:":
                i += 1
            if i >= len(self._buffer):
                return ""
            if self._buffer[i] != '"':
                self._done = True
                return ""
            self._pos = i + 1
        out: list[str] = []
        buf, i = self._buffer, self._pos
        while i < len(buf):
            char = buf[i]
            if char == '"':
                self._done = True
                i += 1
                break
            if char != "\\":
                out.append(char)
                i += 1
                continue
            if i + 1 >= len(buf):
                break  # escape split across chunks; wait for the rest
            code = buf[i + 1]
            if code != "u":
                out.append(_JSON_ESCAPES.get(code, code))
                i += 2
                continue
            if i + 6 > len(buf):
                break
            value = int(buf[i + 2 : i + 6], 16)
            if 0xD800 <= value <= 0xDBFF:  # high surrogate: needs its pair
                if i + 12 > len(buf):
                    break
                low = int(buf[i + 8 : i + 12], 16)
                value = 0x10000 + ((value - 0xD800) << 10) + (low - 0xDC00)
                i += 12
            else:
                i += 6
            out.append(chr(value))
        self._pos = i
        return "".join(out)


@dataclass
class SDKRunResult:
    final_text: str
    trace_id: str | None
    llm_call_count: int
    usage: dict[str, Any] | None
    total_cost_usd: float | None
    first_final_text_delta_elapsed_s: float | None = None


class ClaudeSDKRunner:
    """Runs the query -> receive_response loop for Claude Agent SDK."""

    def __init__(
        self,
        *,
        client_cls: type = ClaudeSDKClient,
        assistant_message_cls: type = AssistantMessage,
        result_message_cls: type = ResultMessage,
        stream_event_cls: type = StreamEvent,
    ):
        self.client_cls = client_cls
        self.assistant_message_cls = assistant_message_cls
        self.result_message_cls = result_message_cls
        self.stream_event_cls = stream_event_cls

    async def _receive(self, client, should_cancel):
        iterator = client.receive_response().__aiter__()
        while True:
            pending = asyncio.ensure_future(anext(iterator))
            try:
                while not pending.done():
                    await asyncio.wait({pending}, timeout=0.05)
                    if not pending.done() and should_cancel and should_cancel():
                        raise TurnCancelled()
                try:
                    message = pending.result()
                except StopAsyncIteration:
                    return
                if (
                    should_cancel
                    and should_cancel()
                    and not isinstance(message, self.result_message_cls)
                ):
                    raise TurnCancelled()
                yield message
            finally:
                if not pending.done():
                    pending.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pending

    async def run(
        self,
        *,
        options: Any,
        prompt_text: str,
        on_text_delta: Callable[[str], None] | None = None,
        on_first_final_text_delta: Callable[[], None] | None = None,
        on_tool_use_start: Callable[[], None] | None = None,
        on_answer_start: Callable[[], None] | None = None,
        on_answer_delta: Callable[[str], None] | None = None,
        should_cancel: CancelCheck | None = None,
    ) -> SDKRunResult:
        final_text = ""
        trace_id = None
        llm_call_count = 0
        usage: dict[str, Any] | None = None
        total_cost_usd: float | None = None
        pending_first_text_delta_elapsed_s: float | None = None
        final_text_delta_candidate_elapsed_s: float | None = None
        first_final_text_delta_elapsed_s: float | None = None
        first_final_text_delta_notified = False
        saw_tool_use_message = False
        respond_text: str | None = None
        respond_streams: dict[Any, JsonStringFieldStream] = {}
        start_time = time.time()

        def notify_first_final_text_delta() -> None:
            nonlocal first_final_text_delta_notified
            if first_final_text_delta_notified or not on_first_final_text_delta:
                return
            first_final_text_delta_notified = True
            on_first_final_text_delta()

        # Checked before the client is opened, not just per streamed message:
        # entering the context manager spawns the agent subprocess and query()
        # submits a billable request, both of which happen before the in-loop
        # check below would ever run. run() is entered more than once per turn
        # — the link-repair pass in turn_orchestrator.py is a second call — so
        # a stop that landed during link validation would otherwise still buy a
        # full repair query.
        if should_cancel and should_cancel():
            raise TurnCancelled()

        async with self.client_cls(options=options) as client:
            await client.query(prompt_text)
            async for message in self._receive(client, should_cancel):
                if isinstance(message, self.assistant_message_cls):
                    llm_call_count += 1
                if isinstance(message, self.result_message_cls):
                    if final_text_delta_candidate_elapsed_s is not None:
                        notify_first_final_text_delta()
                    usage = message.usage
                    total_cost_usd = message.total_cost_usd
                    first_final_text_delta_elapsed_s = final_text_delta_candidate_elapsed_s
                elif isinstance(message, self.stream_event_cls):
                    event = self._stream_event(message)
                    if event.get("type") == "message_start":
                        respond_streams.clear()
                    # A tool call starting means the text streamed just before
                    # it was narration, not the final answer.
                    if self.stream_event_starts_tool_use(message):
                        if on_tool_use_start:
                            on_tool_use_start()
                        block = event.get("content_block") or {}
                        if str(block.get("name", "")).endswith(_RESPOND_TOOL_SUFFIX):
                            respond_streams[event.get("index")] = JsonStringFieldStream("text")
                            if on_answer_start:
                                on_answer_start()
                    answer_delta = self._respond_delta(event, respond_streams)
                    if answer_delta and on_answer_delta:
                        on_answer_delta(answer_delta)
                    delta = self.extract_text_delta_from_stream_event(message)
                    if delta and pending_first_text_delta_elapsed_s is None:
                        pending_first_text_delta_elapsed_s = time.time() - start_time
                    # Notify before forwarding the delta (not after) so a caller
                    # using on_first_final_text_delta to gate on_text_delta
                    # forwarding (e.g. only streaming final-answer content to
                    # the client, not pre-tool-use narration) sees the flag
                    # flip before this delta arrives, not one delta late.
                    if delta and saw_tool_use_message:
                        notify_first_final_text_delta()
                    if delta and on_text_delta:
                        on_text_delta(delta)
                else:
                    chunk = self.extract_text_from_message(message)
                    uses_tools = self.message_uses_tools(message)
                    delivered = self.respond_text_from_message(message)
                    if delivered is not None:
                        respond_text = delivered
                    if isinstance(message, self.assistant_message_cls) and uses_tools:
                        # A tool call proves whatever text came before this point
                        # (including any narration in this same message, e.g.
                        # "Let me also check the calendar...") wasn't the final
                        # answer -- the turn isn't over. Discard it; the real
                        # final text, if any, is whatever text-only message(s)
                        # follow the *last* tool call.
                        final_text = ""
                        saw_tool_use_message = True
                    elif chunk:
                        final_text += chunk
                        final_text_delta_candidate_elapsed_s = (
                            pending_first_text_delta_elapsed_s
                            if pending_first_text_delta_elapsed_s is not None
                            else time.time() - start_time
                        )
                        if saw_tool_use_message:
                            notify_first_final_text_delta()
                    if isinstance(message, self.assistant_message_cls):
                        pending_first_text_delta_elapsed_s = None

            trace_id = getattr(client, "trace_id", None) or getattr(client, "last_trace_id", None)

        return SDKRunResult(
            # The answer the agent delivered through the respond tool; its last
            # plain text only if it never called it.
            final_text=respond_text if respond_text is not None else final_text,
            trace_id=trace_id,
            llm_call_count=llm_call_count,
            usage=usage,
            total_cost_usd=total_cost_usd,
            first_final_text_delta_elapsed_s=first_final_text_delta_elapsed_s,
        )

    @staticmethod
    def _stream_event(message: Any) -> dict:
        event = (
            message.get("event") if isinstance(message, dict) else getattr(message, "event", None)
        )
        return event if isinstance(event, dict) else {}

    @staticmethod
    def _respond_delta(event: dict, respond_streams: dict) -> str:
        if event.get("type") != "content_block_delta":
            return ""
        stream = respond_streams.get(event.get("index"))
        delta = event.get("delta") or {}
        if stream is None or delta.get("type") != "input_json_delta":
            return ""
        return stream.feed(delta.get("partial_json") or "")

    @staticmethod
    def respond_text_from_message(message: Any) -> str | None:
        content = (
            message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        )
        if not isinstance(content, list):
            return None
        for block in content:
            name = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
            tool_input = block.get("input") if isinstance(block, dict) else getattr(block, "input", None)
            if str(name or "").endswith(_RESPOND_TOOL_SUFFIX) and isinstance(tool_input, dict):
                text = tool_input.get("text")
                if isinstance(text, str):
                    return text
        return None

    @staticmethod
    def stream_event_starts_tool_use(message: Any) -> bool:
        event = (
            message.get("event") if isinstance(message, dict) else getattr(message, "event", None)
        )
        if not isinstance(event, dict) or event.get("type") != "content_block_start":
            return False
        block = event.get("content_block")
        return isinstance(block, dict) and block.get("type") in {
            "tool_use",
            "server_tool_use",
            "mcp_tool_use",
        }

    @staticmethod
    def extract_text_delta_from_stream_event(message: Any) -> str:
        event = (
            message.get("event") if isinstance(message, dict) else getattr(message, "event", None)
        )
        if not isinstance(event, dict):
            return ""

        delta = event.get("delta")
        if isinstance(delta, dict):
            delta_type = delta.get("type")
            if delta_type == "text_delta" and isinstance(delta.get("text"), str):
                return delta["text"]

        if event.get("type") == "content_block_delta" and isinstance(delta, dict):
            text = delta.get("text")
            return text if isinstance(text, str) else ""

        return ""

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

    @staticmethod
    def message_uses_tools(message: Any) -> bool:
        content = (
            message.get("content")
            if isinstance(message, dict)
            else getattr(message, "content", None)
        )
        if not isinstance(content, list):
            return False
        for block in content:
            # The SDK's ToolUseBlock has no `type` field, so match it by class.
            if isinstance(block, ToolUseBlock):
                return True
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return True
            if getattr(block, "type", None) == "tool_use":
                return True
        return False
