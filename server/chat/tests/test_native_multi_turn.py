"""Tests for native multi-turn: SDK session resume wiring."""

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import ChatSession
from chat.tests.test_streaming_integration import create_test_token
from chat.V2.agent import AgentResponse
from chat.V2.agent.contracts import ConversationMessage, MessageContext
from chat.V2.agent.sdk_options_builder import SDKOptionsBuilder
from chat.V2.agent.sdk_runner import ClaudeSDKRunner, SDKRunResult
from chat.V2.agent.session_store import PostgresSessionStore
from chat.V2.agent.turn_orchestrator import TurnOrchestrator

# ---------------------------------------------------------------------------
# SDKOptionsBuilder
# ---------------------------------------------------------------------------


def _build(auto_compact_window="", **kwargs):
    builder = SDKOptionsBuilder(
        options_cls=MagicMock(spec=[]),
        model="claude-test",
        max_tokens=1000,
        temperature=0.5,
        braintrust_api_key="bt",
        braintrust_project="proj",
        mcp_server_name="sefaria",
        session_cwd="/server",
        auto_compact_window=auto_compact_window,
    )
    with patch.object(builder, "_supports_option", return_value=True):
        builder.build(system_prompt="SYS", mcp_server=MagicMock(), allowed_tools=[], **kwargs)
    return builder.options_cls.call_args[1]


def test_builder_without_store_is_unchanged():
    options = _build()
    assert options["system_prompt"] == "SYS"
    assert "session_store" not in options
    assert "resume" not in options
    assert "cwd" not in options


def test_builder_with_store_resumes_and_disables_prompt_snapshot():
    store = object()
    options = _build(session_store=store, resume="sdk-1")
    assert options["session_store"] is store
    assert options["resume"] == "sdk-1"
    assert options["cwd"] == "/server"
    assert options["system_prompt"] == {"type": "custom", "prompt": "SYS", "snapshot": False}


def test_builder_sets_compact_window_only_for_stored_sessions():
    assert (
        _build(auto_compact_window="200k", session_store=object())["env"][
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
        ]
        == "200k"
    )
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in _build(auto_compact_window="200k")["env"]
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in _build(session_store=object())["env"]


# ---------------------------------------------------------------------------
# ClaudeSDKRunner
# ---------------------------------------------------------------------------


@dataclass
class FakeResultMessage:
    usage: dict
    total_cost_usd: float
    session_id: str


@dataclass
class FakeSystemMessage:
    subtype: str
    data: dict


class FakeClient:
    def __init__(self, *, options):
        self.options = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt_text):
        pass

    async def receive_response(self):
        yield FakeSystemMessage(subtype="compact_boundary", data={})
        yield {"content": [{"type": "text", "text": "answer"}]}
        yield FakeResultMessage(usage={}, total_cost_usd=0.01, session_id="sdk-new")


def test_runner_returns_result_session_id():
    runner = ClaudeSDKRunner(client_cls=FakeClient, result_message_cls=FakeResultMessage)
    result = asyncio.run(runner.run(options=None, prompt_text="hi"))
    assert result.final_text == "answer"
    assert result.session_id == "sdk-new"
    assert result.compaction_count == 1


# ---------------------------------------------------------------------------
# TurnOrchestrator
# ---------------------------------------------------------------------------


def _sdk_result(text="Plain answer", session_id="sdk-new"):
    return SDKRunResult(
        final_text=text,
        trace_id=None,
        llm_call_count=1,
        usage=None,
        total_cost_usd=None,
        session_id=session_id,
    )


def _orchestrator(sdk_runner, session_store):
    prompt = MagicMock(text="core", prompt_id="core-id", version="1")
    options_builder = MagicMock(thinking_disabled=False)
    options_builder.build.side_effect = lambda **kw: (kw, True)
    guardrail_gate = MagicMock(run_guardrail=AsyncMock(return_value=None))
    router = MagicMock(
        run_router=AsyncMock(side_effect=lambda span, msg, msgs: (None, "discovery", msgs))
    )
    return TurnOrchestrator(
        model="claude-test",
        mcp_server_name="sefaria",
        prompt_service=MagicMock(get_core_prompt=MagicMock(return_value=prompt)),
        create_mcp_server=MagicMock(),
        tool_runtime=MagicMock(build_sdk_tools=MagicMock(return_value=[])),
        options_builder=options_builder,
        sdk_runner=sdk_runner,
        guardrail_gate=guardrail_gate,
        router=router,
        trace_logger=MagicMock(),
        session_store=session_store,
    )


def _run_turn(orchestrator, resume_id):
    return asyncio.run(
        orchestrator.run_turn(
            messages=[ConversationMessage(role="user", content="And what about Rashi?")],
            core_prompt_id=None,
            on_progress=None,
            context=MessageContext(
                sdk_resume_session_id=resume_id,
                summary_text="SUMMARY",
                page_url="https://www.sefaria.org/Genesis.1",
            ),
        )
    )


def _valid_links():
    validator = MagicMock()
    validator.validate_response = AsyncMock(return_value=MagicMock(is_valid=True))
    return patch("chat.V2.agent.turn_orchestrator.ResponseLinkValidator", return_value=validator)


def test_orchestrator_resumes_stored_session():
    store = object()
    sdk_runner = MagicMock(run=AsyncMock(return_value=_sdk_result()))
    with _valid_links():
        response = _run_turn(_orchestrator(sdk_runner, store), "sdk-old")

    options = sdk_runner.run.call_args.kwargs["options"]
    assert options["session_store"] is store
    assert options["resume"] == "sdk-old"
    assert response.sdk_session_id == "sdk-new"


def test_orchestrator_without_store_ignores_resume_id():
    sdk_runner = MagicMock(run=AsyncMock(return_value=_sdk_result()))
    with _valid_links():
        response = _run_turn(_orchestrator(sdk_runner, None), "sdk-old")

    options = sdk_runner.run.call_args.kwargs["options"]
    assert options["session_store"] is None
    assert options["resume"] is None
    assert response.sdk_session_id is None


def test_orchestrator_falls_back_to_fresh_session_when_resume_fails():
    sdk_runner = MagicMock(
        run=AsyncMock(side_effect=[RuntimeError("no transcript"), _sdk_result()])
    )
    with _valid_links():
        response = _run_turn(_orchestrator(sdk_runner, object()), "sdk-old")

    resumes = [call.kwargs["options"]["resume"] for call in sdk_runner.run.call_args_list]
    assert resumes == ["sdk-old", None]
    assert response.content == "Plain answer"
    assert response.sdk_session_id == "sdk-new"


def test_orchestrator_fresh_session_failure_is_not_retried():
    sdk_runner = MagicMock(run=AsyncMock(side_effect=RuntimeError("boom")))
    with _valid_links(), pytest.raises(RuntimeError):
        _run_turn(_orchestrator(sdk_runner, object()), None)
    assert sdk_runner.run.call_count == 1


def test_link_repair_continues_the_new_session():
    sdk_runner = MagicMock(
        run=AsyncMock(side_effect=[_sdk_result("Draft"), _sdk_result("Fixed", "sdk-repaired")])
    )
    validator = MagicMock()
    validator.validate_response = AsyncMock(
        side_effect=[MagicMock(is_valid=False, issues=[]), MagicMock(is_valid=True)]
    )
    with (
        patch("chat.V2.agent.turn_orchestrator.ResponseLinkValidator", return_value=validator),
        patch("chat.V2.agent.turn_orchestrator.format_link_validation_issues", return_value=""),
    ):
        response = _run_turn(_orchestrator(sdk_runner, object()), "sdk-old")

    repair_call = sdk_runner.run.call_args_list[1].kwargs
    assert repair_call["options"]["resume"] == "sdk-new"
    assert repair_call["prompt_text"].startswith("The draft response below")
    assert response.content == "Fixed"
    assert response.sdk_session_id == "sdk-repaired"


# ---------------------------------------------------------------------------
# Streaming view
# ---------------------------------------------------------------------------

SECRET = "test-secret-key-for-tokens"


def _post(session_id, message_id="msg_native"):
    response = APIClient().post(
        "/api/v2/chat/stream",
        data={
            "userId": create_test_token("user_native", SECRET),
            "sessionId": session_id,
            "messageId": message_id,
            "timestamp": timezone.now().isoformat(),
            "text": "And what about Rashi?",
        },
        format="json",
    )
    list(response.streaming_content)


def _agent(sdk_session_id="sdk-new"):
    agent = MagicMock()
    agent.send_message = AsyncMock(
        return_value=AgentResponse(
            content="Rashi says...",
            tool_calls=[],
            latency_ms=10,
            trace_id="trace",
            sdk_session_id=sdk_session_id,
        )
    )
    return agent


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET, NATIVE_MULTI_TURN_ENABLED=True)
@patch("chat.V2.views.get_agent_service")
def test_view_resumes_head_and_persists_new_one(mock_get_agent):
    ChatSession.objects.create(
        session_id="sess_native", user_id="user_native", sdk_session_id="sdk-old"
    )
    agent = _agent()
    mock_get_agent.return_value = agent

    _post("sess_native")

    assert isinstance(mock_get_agent.call_args.kwargs["session_store"], PostgresSessionStore)
    context = agent.send_message.call_args.kwargs["context"]
    assert context.sdk_resume_session_id == "sdk-old"
    assert ChatSession.objects.get(session_id="sess_native").sdk_session_id == "sdk-new"


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET, NATIVE_MULTI_TURN_ENABLED=False)
@patch("chat.V2.views.get_agent_service")
def test_view_disabled_passes_no_store(mock_get_agent):
    mock_get_agent.return_value = _agent(sdk_session_id=None)

    _post("sess_disabled")

    assert mock_get_agent.call_args.kwargs["session_store"] is None
    assert ChatSession.objects.get(session_id="sess_disabled").sdk_session_id is None


def test_resumed_turn_sends_stable_system_prompt_and_only_the_new_message():
    sdk_runner = MagicMock(run=AsyncMock(return_value=_sdk_result()))
    with _valid_links():
        _run_turn(_orchestrator(sdk_runner, object()), "sdk-old")

    call = sdk_runner.run.call_args.kwargs
    assert call["options"]["system_prompt"] == "core"
    assert "SUMMARY" not in call["prompt_text"]
    assert "Genesis.1" in call["prompt_text"]
    assert call["prompt_text"].endswith("And what about Rashi?")
    assert not call["prompt_text"].startswith("User:")


def test_fresh_native_turn_includes_summary():
    sdk_runner = MagicMock(run=AsyncMock(return_value=_sdk_result()))
    with _valid_links():
        _run_turn(_orchestrator(sdk_runner, object()), None)

    call = sdk_runner.run.call_args.kwargs
    assert call["options"]["system_prompt"] == "core"
    assert "SUMMARY" in call["prompt_text"]


def test_resume_fallback_prompt_includes_summary():
    sdk_runner = MagicMock(
        run=AsyncMock(side_effect=[RuntimeError("no transcript"), _sdk_result()])
    )
    with _valid_links():
        _run_turn(_orchestrator(sdk_runner, object()), "sdk-old")

    first, fallback = (call.kwargs["prompt_text"] for call in sdk_runner.run.call_args_list)
    assert "SUMMARY" not in first
    assert "SUMMARY" in fallback


def test_legacy_turn_keeps_full_system_prompt():
    sdk_runner = MagicMock(run=AsyncMock(return_value=_sdk_result()))
    with _valid_links():
        _run_turn(_orchestrator(sdk_runner, None), None)

    system_prompt = sdk_runner.run.call_args.kwargs["options"]["system_prompt"]
    assert "SUMMARY" in system_prompt
    assert "And what about Rashi?" in system_prompt
