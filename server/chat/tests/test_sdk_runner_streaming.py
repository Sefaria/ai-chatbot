import asyncio
from dataclasses import dataclass

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
