import asyncio
from dataclasses import dataclass

import pytest

import chat.V2.agent.sdk_runner as sdk_runner_module
from chat.V2.agent.contracts import TurnCancelled
from chat.V2.agent.sdk_runner import ClaudeSDKRunner


@dataclass
class FakeAssistantMessage:
    content: list[dict]


@dataclass
class FakeResultMessage:
    usage: dict
    total_cost_usd: float


@dataclass
class FakeStreamEvent:
    event: dict


class FakeClient:
    def __init__(self, *, options):
        self.options = options
        self.trace_id = "trace-stream"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def query(self, prompt_text):
        self.prompt_text = prompt_text

    async def receive_response(self):
        yield FakeStreamEvent(
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Shalom"},
            }
        )
        yield FakeStreamEvent(
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": " world"},
            }
        )
        yield FakeAssistantMessage(content=[{"type": "text", "text": "Shalom world"}])
        yield FakeResultMessage(usage={"input_tokens": 1}, total_cost_usd=0.01)


class FakeToolThenFinalClient(FakeClient):
    async def receive_response(self):
        yield FakeStreamEvent(
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Let me check"},
            }
        )
        yield FakeAssistantMessage(content=[{"type": "text", "text": "Let me check"}])
        yield FakeStreamEvent(
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "I will search"},
            }
        )
        yield FakeAssistantMessage(
            content=[
                {"type": "text", "text": "I will search"},
                {"type": "tool_use", "name": "semantic_search", "input": {}},
            ]
        )
        yield FakeStreamEvent(
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Final"},
            }
        )
        yield FakeAssistantMessage(content=[{"type": "text", "text": "Final answer"}])
        yield FakeResultMessage(usage={"input_tokens": 1}, total_cost_usd=0.01)


def test_stream_event_text_deltas_are_observed_without_changing_final_text():
    runner = ClaudeSDKRunner(
        client_cls=FakeClient,
        assistant_message_cls=FakeAssistantMessage,
        result_message_cls=FakeResultMessage,
        stream_event_cls=FakeStreamEvent,
    )
    deltas = []

    result = asyncio.run(
        runner.run(options=object(), prompt_text="prompt", on_text_delta=deltas.append)
    )

    assert deltas == ["Shalom", " world"]
    assert result.final_text == "Shalom world"
    assert result.trace_id == "trace-stream"
    assert result.llm_call_count == 1
    assert result.first_final_text_delta_elapsed_s is not None


def test_first_final_text_delta_uses_last_text_message_before_result(monkeypatch):
    timestamps = iter([100.0, 101.0, 102.0, 110.0])
    monkeypatch.setattr(sdk_runner_module.time, "time", lambda: next(timestamps))
    runner = ClaudeSDKRunner(
        client_cls=FakeToolThenFinalClient,
        assistant_message_cls=FakeAssistantMessage,
        result_message_cls=FakeResultMessage,
        stream_event_cls=FakeStreamEvent,
    )

    result = asyncio.run(runner.run(options=object(), prompt_text="prompt"))

    # "Let me check" precedes no tool call, so it would be the final answer if
    # nothing followed -- but "I will search" is itself followed by a tool_use
    # block, so it (and everything before it) gets discarded once that's seen.
    # Only text after the *last* tool call -- "Final answer" -- survives.
    assert result.final_text == "Final answer"
    assert result.llm_call_count == 3
    assert result.first_final_text_delta_elapsed_s == 10.0


def test_final_text_discards_a_complete_sounding_answer_before_a_later_tool_call():
    """Regression test for a real production bug: the model can write what reads
    as a whole, complete answer, then decide to call *another* tool and write a
    second, different complete answer. Naively concatenating every message's
    text (the old behavior) produced both answers glued together. Only the
    text after the last tool call is the real final answer.
    """

    class TwoToolRoundsClient(FakeClient):
        async def receive_response(self):
            yield FakeAssistantMessage(
                content=[
                    {"type": "text", "text": "Let me check the calendar."},
                    {"type": "tool_use", "name": "get_current_calendar", "input": {}},
                ]
            )
            yield FakeAssistantMessage(
                content=[
                    {
                        "type": "text",
                        "text": "Today's Daf Yomi is Bekhorot 33. It concerns blemishes.",
                    },
                    {"type": "tool_use", "name": "semantic_search", "input": {}},
                ]
            )
            yield FakeAssistantMessage(
                content=[
                    {
                        "type": "text",
                        "text": "Today's Daf Yomi is Bekhorot 33, with linked sources.",
                    }
                ]
            )
            yield FakeResultMessage(usage={"input_tokens": 1}, total_cost_usd=0.01)

    runner = ClaudeSDKRunner(
        client_cls=TwoToolRoundsClient,
        assistant_message_cls=FakeAssistantMessage,
        result_message_cls=FakeResultMessage,
        stream_event_cls=FakeStreamEvent,
    )

    result = asyncio.run(runner.run(options=object(), prompt_text="prompt"))

    assert result.final_text == "Today's Daf Yomi is Bekhorot 33, with linked sources."


def test_final_text_still_concatenates_a_final_answer_split_across_messages():
    """Not every tool-less message after the last tool call is narration -- a
    long final answer can legitimately arrive split across multiple messages
    (e.g. the SDK continuing past a length limit) with no tool call in
    between. Those must still be concatenated, not overwritten.
    """

    class SplitFinalAnswerClient(FakeClient):
        async def receive_response(self):
            yield FakeAssistantMessage(
                content=[
                    {"type": "text", "text": "Searching."},
                    {"type": "tool_use", "name": "semantic_search", "input": {}},
                ]
            )
            yield FakeAssistantMessage(content=[{"type": "text", "text": "Part one. "}])
            yield FakeAssistantMessage(content=[{"type": "text", "text": "Part two."}])
            yield FakeResultMessage(usage={"input_tokens": 1}, total_cost_usd=0.01)

    runner = ClaudeSDKRunner(
        client_cls=SplitFinalAnswerClient,
        assistant_message_cls=FakeAssistantMessage,
        result_message_cls=FakeResultMessage,
        stream_event_cls=FakeStreamEvent,
    )

    result = asyncio.run(runner.run(options=object(), prompt_text="prompt"))

    assert result.final_text == "Part one. Part two."


def test_first_final_text_delta_callback_fires_before_the_final_delta_it_gates():
    # on_first_final_text_delta must fire before the delta it's meant to gate,
    # not after -- callers that use it to decide whether to start forwarding
    # on_text_delta content to a client (e.g. streaming only the final-answer
    # text, never pre-tool-use narration) need the flag to already be true by
    # the time that first "real" delta ("Final") arrives, not one delta late.
    runner = ClaudeSDKRunner(
        client_cls=FakeToolThenFinalClient,
        assistant_message_cls=FakeAssistantMessage,
        result_message_cls=FakeResultMessage,
        stream_event_cls=FakeStreamEvent,
    )
    events = []

    asyncio.run(
        runner.run(
            options=object(),
            prompt_text="prompt",
            on_text_delta=lambda delta: events.append(delta),
            on_first_final_text_delta=lambda: events.append("final-started"),
        )
    )

    assert events == ["Let me check", "I will search", "final-started", "Final"]


def test_message_uses_tools_detects_real_sdk_tool_use_block():
    # The SDK's ToolUseBlock has no `type` field, unlike the dict fakes above.
    # Missing it meant the runner never saw tool use, so final-answer streaming
    # never started.
    from claude_agent_sdk.types import TextBlock, ToolUseBlock

    tool_message = FakeAssistantMessage(content=[ToolUseBlock(id="t1", name="search", input={})])
    text_message = FakeAssistantMessage(content=[TextBlock(text="Final answer")])

    assert ClaudeSDKRunner.message_uses_tools(tool_message)
    assert not ClaudeSDKRunner.message_uses_tools(text_message)


class FakeCancelTrackingClient(FakeClient):
    """Records whether the client was closed, so we can prove the subprocess dies."""

    def __init__(self, *, options):
        super().__init__(options=options)
        self.closed = False
        self.messages_yielded = 0

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    async def receive_response(self):
        for _ in range(5):
            self.messages_yielded += 1
            yield FakeAssistantMessage(content=[{"type": "text", "text": "chunk"}])
        yield FakeResultMessage(usage={"input_tokens": 1}, total_cost_usd=0.01)


def test_run_completes_when_should_cancel_stays_false():
    runner = ClaudeSDKRunner(
        client_cls=FakeClient,
        assistant_message_cls=FakeAssistantMessage,
        result_message_cls=FakeResultMessage,
        stream_event_cls=FakeStreamEvent,
    )

    result = asyncio.run(
        runner.run(options=object(), prompt_text="prompt", should_cancel=lambda: False)
    )

    assert result.final_text == "Shalom world"


def test_run_raises_turn_cancelled_and_closes_client():
    """Unwinding out of `async with` is what terminates the agent subprocess."""
    clients = []

    class TrackingFactory(FakeCancelTrackingClient):
        def __init__(self, *, options):
            super().__init__(options=options)
            clients.append(self)

    runner = ClaudeSDKRunner(
        client_cls=TrackingFactory,
        assistant_message_cls=FakeAssistantMessage,
        result_message_cls=FakeResultMessage,
        stream_event_cls=FakeStreamEvent,
    )

    # Runs normally for two messages, then the user hits stop. Keyed off
    # messages actually delivered rather than a count of should_cancel() calls,
    # so the test stays about cancellation behaviour and does not break when the
    # runner legitimately checks the flag somewhere new.
    def should_cancel():
        return bool(clients) and clients[0].messages_yielded >= 3

    with pytest.raises(TurnCancelled):
        asyncio.run(runner.run(options=object(), prompt_text="prompt", should_cancel=should_cancel))

    assert clients[0].closed is True
    assert clients[0].messages_yielded == 3


def test_cancel_is_checked_before_the_first_message_is_processed():
    runner = ClaudeSDKRunner(
        client_cls=FakeCancelTrackingClient,
        assistant_message_cls=FakeAssistantMessage,
        result_message_cls=FakeResultMessage,
        stream_event_cls=FakeStreamEvent,
    )

    with pytest.raises(TurnCancelled):
        asyncio.run(runner.run(options=object(), prompt_text="prompt", should_cancel=lambda: True))


def test_cancel_before_the_run_submits_no_billable_request():
    """A stop that landed before this run must not cost anything.

    The per-message check above still lets the expensive part happen first:
    opening the client spawns the agent subprocess and query() submits the
    request. run() is called a second time for the link-repair pass, so a stop
    arriving during link validation used to buy a full repair query — whole
    prompt plus draft answer — before the first check was reached.
    """
    opened = []

    class TrackingFactory(FakeCancelTrackingClient):
        def __init__(self, *, options):
            super().__init__(options=options)
            self.queried = None
            opened.append(self)

        async def query(self, prompt_text):
            self.queried = prompt_text

    runner = ClaudeSDKRunner(
        client_cls=TrackingFactory,
        assistant_message_cls=FakeAssistantMessage,
        result_message_cls=FakeResultMessage,
        stream_event_cls=FakeStreamEvent,
    )

    with pytest.raises(TurnCancelled):
        asyncio.run(runner.run(options=object(), prompt_text="prompt", should_cancel=lambda: True))

    assert opened == [], "the SDK client was opened for a turn that was already cancelled"


@pytest.mark.asyncio
async def test_cancel_interrupts_a_silent_sdk_wait():
    import asyncio

    from chat.V2.agent.contracts import TurnCancelled

    cancelled = False
    closed = False

    class SilentClient(FakeClient):
        async def receive_response(self):
            await asyncio.sleep(10)
            if False:
                yield None

        async def __aexit__(self, *args):
            nonlocal closed
            closed = True

    async def request_stop():
        nonlocal cancelled
        await asyncio.sleep(0.05)
        cancelled = True

    timer = asyncio.create_task(request_stop())
    runner = ClaudeSDKRunner(client_cls=SilentClient)
    with pytest.raises(TurnCancelled):
        await asyncio.wait_for(
            runner.run(options={}, prompt_text="q", should_cancel=lambda: cancelled), 1
        )
    await timer
    assert closed


def test_stream_event_starts_tool_use_detects_tool_blocks_only():
    tool_start = FakeStreamEvent(
        event={"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t1", "name": "search"}}
    )
    text_start = FakeStreamEvent(
        event={"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
    )
    text_delta = FakeStreamEvent(
        event={"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}}
    )

    assert ClaudeSDKRunner.stream_event_starts_tool_use(tool_start)
    assert not ClaudeSDKRunner.stream_event_starts_tool_use(text_start)
    assert not ClaudeSDKRunner.stream_event_starts_tool_use(text_delta)


class FakeNarrationThenRespondClient(FakeClient):
    """Narrates, calls a tool, then delivers the answer through respond."""

    async def receive_response(self):
        def delta(index, text):
            return FakeStreamEvent(
                event={"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}}
            )

        def tool_start(index, name):
            return FakeStreamEvent(
                event={"type": "content_block_start", "index": index, "content_block": {"type": "tool_use", "name": name}}
            )

        def json_delta(index, partial):
            return FakeStreamEvent(
                event={"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": partial}}
            )

        yield FakeStreamEvent(event={"type": "message_start"})
        yield delta(0, "Let me search.")
        yield tool_start(1, "mcp__sefaria__semantic_search")
        yield FakeAssistantMessage(
            content=[{"type": "text", "text": "Let me search."}, {"type": "tool_use", "name": "mcp__sefaria__semantic_search", "input": {}}]
        )
        yield FakeStreamEvent(event={"type": "message_start"})
        yield tool_start(0, "mcp__sefaria__respond")
        yield json_delta(0, '{"te')
        yield json_delta(0, 'xt": "Sha')
        yield json_delta(0, 'lom \\"wor')
        yield json_delta(0, 'ld\\""}')
        yield FakeStreamEvent(event={"type": "content_block_stop", "index": 0})
        yield FakeAssistantMessage(
            content=[{"type": "tool_use", "name": "mcp__sefaria__respond", "input": {"text": 'Shalom "world"'}}]
        )
        yield FakeResultMessage(usage={"input_tokens": 1}, total_cost_usd=0.01)


def test_respond_tool_text_streams_as_the_answer_and_plain_text_as_narration():
    runner = ClaudeSDKRunner(
        client_cls=FakeNarrationThenRespondClient,
        assistant_message_cls=FakeAssistantMessage,
        result_message_cls=FakeResultMessage,
        stream_event_cls=FakeStreamEvent,
    )
    events = []

    result = asyncio.run(
        runner.run(
            options=object(),
            prompt_text="prompt",
            on_text_delta=lambda d: events.append(("narration", d)),
            on_tool_use_start=lambda: events.append(("tool",)),
            on_answer_start=lambda: events.append(("answer_start",)),
            on_answer_delta=lambda d: events.append(("answer", d)),
            on_answer_end=lambda: events.append(("answer_end",)),
        )
    )

    answer = "".join(e[1] for e in events if e[0] == "answer")
    # The answer is done when its tool block closes, right after its last piece.
    last_answer = max(i for i, e in enumerate(events) if e[0] == "answer")
    assert events[last_answer + 1] == ("answer_end",)
    assert events[0] == ("narration", "Let me search.")
    assert events[1] == ("tool",)
    assert ("answer_start",) in events
    assert answer == 'Shalom "world"'
    assert result.final_text == 'Shalom "world"'


def test_final_text_falls_back_to_plain_text_without_respond():
    runner = ClaudeSDKRunner(
        client_cls=FakeClient,
        assistant_message_cls=FakeAssistantMessage,
        result_message_cls=FakeResultMessage,
        stream_event_cls=FakeStreamEvent,
    )

    result = asyncio.run(runner.run(options=object(), prompt_text="prompt"))

    assert result.final_text == "Shalom world"


@pytest.mark.parametrize("size", [1, 2, 3, 7])
def test_json_string_field_stream_decodes_across_any_chunking(size):
    import json

    from chat.V2.agent.sdk_runner import JsonStringFieldStream

    text = 'Line "one"\nback\\slash, é and 😀'
    raw = json.dumps({"text": text})
    stream = JsonStringFieldStream("text")

    assert "".join(stream.feed(raw[i : i + size]) for i in range(0, len(raw), size)) == text
