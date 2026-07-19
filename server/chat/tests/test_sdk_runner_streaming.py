import asyncio
from dataclasses import dataclass

import chat.V2.agent.sdk_runner as sdk_runner_module
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
