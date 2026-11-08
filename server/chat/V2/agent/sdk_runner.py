"""Claude Agent SDK execution loop and message text extraction."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, StreamEvent, ToolUseBlock

from .contracts import CancelCheck, TurnCancelled


@dataclass
class SDKRunResult:
    final_text: str
    trace_id: str | None
    llm_call_count: int
    usage: dict[str, Any] | None
    total_cost_usd: float | None
    first_final_text_delta_elapsed_s: float | None = None
    # Text discarded from final_text because a later tool call proved it wasn't
    # the final answer, in the order it was superseded -- i.e. the narration
    # rounds that preceded the real answer.
    narration_segments: list[str] = field(default_factory=list)


#: Ends each streamed text block so passages split by a tool call don't run
#: together in the client's live display.
PASSAGE_BREAK = "\n\n"
#: Inlined into the live delta stream the instant a tool call opens (before
#: its input has even been written), so the client can toggle a "waiting on
#: a lookup" indicator exactly when playback reaches this point. Chosen from
#: the Unicode Private Use Area -- real model text never contains it -- and
#: stripped back out client-side before rendering.
TOOL_WAIT_MARKER = ""
TOOL_CALL_BLOCK_TYPES = {"tool_use", "server_tool_use", "mcp_tool_use"}


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
        should_cancel: CancelCheck | None = None,
    ) -> SDKRunResult:
        final_text = ""
        narration_segments: list[str] = []
        trace_id = None
        llm_call_count = 0
        usage: dict[str, Any] | None = None
        total_cost_usd: float | None = None
        pending_first_text_delta_elapsed_s: float | None = None
        final_text_delta_candidate_elapsed_s: float | None = None
        first_final_text_delta_elapsed_s: float | None = None
        first_final_text_delta_notified = False
        saw_tool_use_message = False
        # Indices of content blocks currently mid-stream, so a block's close
        # can end it with a passage break -- but only the ones that actually
        # streamed text, not e.g. a tool_use block's own index.
        streamed_text_blocks: set[int] = set()
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
                    delta = self.extract_text_delta_from_stream_event(message)
                    if delta and pending_first_text_delta_elapsed_s is None:
                        pending_first_text_delta_elapsed_s = time.time() - start_time
                    # on_first_final_text_delta only drives the time-to-first-
                    # final-token metric now; every delta is forwarded below
                    # regardless of this flag.
                    if delta and saw_tool_use_message:
                        notify_first_final_text_delta()
                    if delta and on_text_delta:
                        on_text_delta(delta)
                    index = self.text_block_index_from_stream_event(message)
                    event_type = self.stream_event_type(message)
                    if delta and index is not None:
                        streamed_text_blocks.add(index)
                    elif event_type == "message_start":
                        streamed_text_blocks.clear()
                    elif event_type == "content_block_stop" and index in streamed_text_blocks:
                        streamed_text_blocks.discard(index)
                        if on_text_delta:
                            on_text_delta(PASSAGE_BREAK)
                    # A tool call block opening means a wait is starting now,
                    # before the model has finished writing the call's input.
                    if on_text_delta and self.stream_event_starts_tool_call(message):
                        on_text_delta(TOOL_WAIT_MARKER)
                else:
                    chunk = self.extract_text_from_message(message)
                    uses_tools = self.message_uses_tools(message)
                    if isinstance(message, self.assistant_message_cls) and uses_tools:
                        # A tool call proves whatever text came before this point
                        # (including any narration in this same message, e.g.
                        # "Let me also check the calendar...") wasn't the final
                        # answer -- the turn isn't over. It's not thrown away
                        # anymore though: it's one narration round, kept in
                        # order so the caller can persist it. The real final
                        # text, if any, is whatever text-only message(s) follow
                        # the *last* tool call.
                        narration_round = final_text + chunk
                        if narration_round:
                            narration_segments.append(narration_round)
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
            final_text=final_text,
            trace_id=trace_id,
            llm_call_count=llm_call_count,
            usage=usage,
            total_cost_usd=total_cost_usd,
            first_final_text_delta_elapsed_s=first_final_text_delta_elapsed_s,
            narration_segments=narration_segments,
        )

    @staticmethod
    def stream_event_type(message: Any) -> str | None:
        event = (
            message.get("event") if isinstance(message, dict) else getattr(message, "event", None)
        )
        return event.get("type") if isinstance(event, dict) else None

    @staticmethod
    def text_block_index_from_stream_event(message: Any) -> int | None:
        event = (
            message.get("event") if isinstance(message, dict) else getattr(message, "event", None)
        )
        if not isinstance(event, dict):
            return None
        index = event.get("index")
        return index if isinstance(index, int) else None

    @staticmethod
    def stream_event_starts_tool_call(message: Any) -> bool:
        event = (
            message.get("event") if isinstance(message, dict) else getattr(message, "event", None)
        )
        if not isinstance(event, dict) or event.get("type") != "content_block_start":
            return False
        block = event.get("content_block")
        return isinstance(block, dict) and block.get("type") in TOOL_CALL_BLOCK_TYPES

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
