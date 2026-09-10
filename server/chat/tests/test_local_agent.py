"""The thin local agent: config, event translation, and the HTTP surface.

This daemon is the alternative to ``local_runner``. Where that one reuses the
server's agent, this one lets Claude Code be the agent against the public MCP
server — so the things worth pinning here are the ones that would silently
change what the agent can reach or what the widget receives.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from local_agent import agent, config, pairing, session
from local_agent.app import build_app

SEFARIA_IL = "https://www.sefaria.org.il"


@pytest.fixture(autouse=True)
def agent_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SEFARIA_AGENT_HOME", str(tmp_path))
    session.set_code(None)
    yield tmp_path
    session.set_code(None)


@pytest.fixture
def client():
    return TestClient(build_app(), base_url="http://127.0.0.1:8899")


class TestToolAccess:
    """The agent must reach the Sefaria tools and nothing else."""

    def test_only_the_sefaria_mcp_server_is_configured(self):
        options = agent.build_options("prompt")
        assert list(options.mcp_servers) == [config.MCP_SERVER_NAME]

    def test_local_mcp_configuration_is_ignored(self):
        # Otherwise the agent inherits whatever MCP servers the machine has,
        # which is how a personal Figma server ended up in a Sefaria turn.
        assert agent.build_options("prompt").strict_mcp_config is True

    def test_built_in_tools_are_not_allowed(self):
        # bypassPermissions without an allowlist would hand the agent Read,
        # Write and Bash. No filesystem access is a design decision, not an
        # accident of configuration.
        allowed = agent.build_options("prompt").allowed_tools
        assert allowed == [f"mcp__{config.MCP_SERVER_NAME}"]
        assert not any(tool in allowed for tool in ("Read", "Write", "Bash", "Edit"))


class TestTransportChoice:
    def test_sse_url_uses_the_sse_transport(self, monkeypatch):
        # Production serves /sse only; a mismatched transport fails at connect.
        monkeypatch.setenv("SEFARIA_MCP_URL", "https://mcp.sefaria.org/sse")
        assert config.mcp_server_config()["type"] == "sse"

    def test_mcp_url_uses_streamable_http(self, monkeypatch):
        monkeypatch.setenv("SEFARIA_MCP_URL", "http://127.0.0.1:8088/mcp")
        assert config.mcp_server_config()["type"] == "http"


class TestOrigins:
    def test_both_sefaria_domains_are_allowed_by_default(self):
        allowed = config.allowed_origins()
        assert "https://www.sefaria.org" in allowed
        assert SEFARIA_IL in allowed

    def test_extra_origins_are_opt_in(self, monkeypatch):
        monkeypatch.setenv("SEFARIA_ALLOWED_ORIGINS", "http://localhost:5173")
        assert "http://localhost:5173" in config.allowed_origins()


class TestHttpSurface:
    def test_health_needs_no_token(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["paired"] is False

    def test_streaming_without_a_token_is_refused(self, client):
        assert client.post("/api/chat/stream", json={"text": "hi"}).status_code == 401

    def test_a_foreign_origin_is_refused(self, client):
        response = client.get("/health", headers={"Origin": "https://evil.com"})
        assert response.status_code == 401

    def test_a_rebound_host_is_refused(self, client):
        # The daemon binds loopback, so any other host name means a name that
        # resolves to 127.0.0.1 — DNS rebinding.
        assert client.get("/health", headers={"Host": "evil.com"}).status_code == 400

    def test_pairing_needs_the_printed_code(self, client):
        session.set_code("123456")
        response = client.post("/pair", json={"code": "000000", "userId": "tok"})
        assert response.status_code == 403
        assert response.json()["error"] == "invalid_code"

    def test_pairing_is_closed_without_a_code(self, client):
        assert client.post("/pair", json={"code": "1", "userId": "t"}).status_code == 409

    def test_a_paired_token_is_accepted(self, client):
        record = pairing.pair(user_id="u1", sefaria_user_id="s1", encrypted_user_token="tok")
        response = client.post(
            "/api/chat/stream",
            json={"text": ""},
            headers={"Authorization": f"Bearer {record.runner_token}"},
        )
        # Past auth: the turn itself needs Claude, which the test has no session for.
        assert response.status_code != 401


class TestEventTranslation:
    """SDK messages become the events the widget already knows how to render."""

    def test_a_tool_call_opens_and_closes(self):
        result = agent.TurnResult()
        assistant = _assistant_with_tool("mcp__sefaria__get_text", "call-1", {"reference": "Gen 1"})

        opened = agent._events_for(assistant, result)
        closed = agent._events_for(_tool_result("call-1"), result)

        assert opened[0][1]["type"] == "tool_start"
        assert closed[0][1]["type"] == "tool_end"

    def test_tool_names_are_shown_without_the_mcp_prefix(self):
        # These reach a reader in the progress trail.
        result = agent.TurnResult()
        events = agent._events_for(
            _assistant_with_tool("mcp__sefaria__get_text", "call-1", {}), result
        )
        assert events[0][1]["tool_name"] == "get_text"

    def test_a_tool_result_is_paired_with_its_call(self):
        result = agent.TurnResult()
        agent._events_for(_assistant_with_tool("mcp__sefaria__search", "call-9", {}), result)
        closed = agent._events_for(_tool_result("call-9"), result)
        assert closed[0][1]["tool_name"] == "search"

    def test_text_accumulates_into_the_answer(self):
        result = agent.TurnResult()
        agent._events_for(_assistant_text("In the "), result)
        agent._events_for(_assistant_text("beginning."), result)
        assert result.text == "In the beginning."


# --- doubles for SDK message shapes ---------------------------------------


class _Block:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _assistant_text(text: str):
    from claude_agent_sdk.types import AssistantMessage

    return AssistantMessage(content=[_Block(text=text)], model="claude-sonnet-4-6")


def _assistant_with_tool(name: str, block_id: str, tool_input: dict):
    from claude_agent_sdk.types import AssistantMessage

    return AssistantMessage(
        content=[_Block(name=name, id=block_id, input=tool_input, text=None)],
        model="claude-sonnet-4-6",
    )


def _tool_result(tool_use_id: str):
    from claude_agent_sdk.types import UserMessage

    return UserMessage(content=[_Block(tool_use_id=tool_use_id, is_error=False)])


def test_sse_framing_matches_the_widget_contract():
    from local_agent.app import _sse

    frame = _sse("progress", {"type": "status", "text": "Thinking..."})
    assert frame.startswith("event: progress\ndata: ")
    assert frame.endswith("\n\n")
    assert json.loads(frame.split("data: ", 1)[1].strip())["type"] == "status"
