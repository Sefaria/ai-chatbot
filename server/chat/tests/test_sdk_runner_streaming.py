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

    assert result.final_text == "Let me checkI will searchFinal answer"
    assert result.llm_call_count == 3
    assert result.first_final_text_delta_elapsed_s == 10.0


def test_first_final_text_delta_callback_waits_for_final_text():
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

    assert events == ["Let me check", "I will search", "Final", "final-started"]


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
