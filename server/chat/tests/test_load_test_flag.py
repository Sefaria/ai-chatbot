"""Tests for is_load_test feature flag behavior.

Covers:
- ChatRequestSerializer: isLoadTest field parsing and defaults
- ClaudeAgentService: model selection and braintrust_logging_enabled
- SDKOptionsBuilder: braintrust keys omitted from subprocess env when disabled
- get_agent_service factory: flag wired through correctly
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(is_load_test: bool, mock_setup_fn=None):
    """Build a ClaudeAgentService with all external SDK deps patched."""
    mock_setup = mock_setup_fn or MagicMock(return_value=True)

    with (
        patch("chat.V2.agent.claude_service.ClaudeAgentOptions", MagicMock()),
        patch("chat.V2.agent.claude_service.ClaudeSDKClient", MagicMock()),
        patch("chat.V2.agent.claude_service.create_sdk_mcp_server", MagicMock()),
        patch("chat.V2.agent.claude_service.tool", MagicMock()),
        patch("chat.V2.agent.claude_service.setup_claude_agent_sdk", mock_setup),
        patch("chat.V2.agent.claude_service.get_anthropic_client", MagicMock()),
        patch("chat.V2.agent.claude_service.get_prompt_service", MagicMock()),
        patch(
            "chat.V2.agent.claude_service.get_braintrust_config",
            MagicMock(return_value=MagicMock(api_key="bt-key", project="bt-project")),
        ),
        patch("chat.V2.agent.claude_service.SefariaClient", MagicMock()),
        patch("chat.V2.agent.claude_service.SefariaToolExecutor", MagicMock()),
        patch("chat.V2.agent.claude_service.ToolRuntime", MagicMock()),
        patch("chat.V2.agent.claude_service.SDKOptionsBuilder", MagicMock()),
        patch("chat.V2.agent.claude_service.ClaudeSDKRunner", MagicMock()),
        patch("chat.V2.agent.claude_service.DefaultGuardrailGate", MagicMock()),
        patch("chat.V2.agent.claude_service.BraintrustTraceLogger", MagicMock()),
        patch("chat.V2.agent.claude_service.TurnOrchestrator", MagicMock()),
        patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
    ):
        from chat.V2.agent import claude_service

        claude_service._BRAINTRUST_SETUP_DONE = False
        service = claude_service.ClaudeAgentService(is_load_test=is_load_test)

    return service, mock_setup


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------


class TestChatRequestSerializerIsLoadTest:
    """isLoadTest field on ChatRequestSerializer."""

    @pytest.fixture
    def base_data(self):
        return {
            "userId": "user_lt",
            "sessionId": "sess_lt",
            "messageId": "msg_lt",
            "timestamp": "2024-01-15T10:30:00Z",
            "text": "Hello",
        }

    def test_defaults_to_false_when_absent(self, base_data):
        from chat.serializers import ChatRequestSerializer

        s = ChatRequestSerializer(data=base_data)
        assert s.is_valid(), s.errors
        assert s.validated_data["isLoadTest"] is False

    def test_true_accepted(self, base_data):
        from chat.serializers import ChatRequestSerializer

        base_data["isLoadTest"] = True
        s = ChatRequestSerializer(data=base_data)
        assert s.is_valid(), s.errors
        assert s.validated_data["isLoadTest"] is True

    def test_false_explicit(self, base_data):
        from chat.serializers import ChatRequestSerializer

        base_data["isLoadTest"] = False
        s = ChatRequestSerializer(data=base_data)
        assert s.is_valid(), s.errors
        assert s.validated_data["isLoadTest"] is False


# ---------------------------------------------------------------------------
# ClaudeAgentService
# ---------------------------------------------------------------------------


class TestClaudeAgentServiceLoadTestFlag:
    """Model selection and braintrust_logging_enabled set correctly."""

    def test_load_test_disables_braintrust_logging(self):
        service, _ = _make_service(is_load_test=True)
        assert service.braintrust_logging_enabled is False

    def test_normal_mode_enables_braintrust_logging(self):
        service, _ = _make_service(is_load_test=False)
        assert service.braintrust_logging_enabled is True

    @override_settings(AGENT_MODEL="claude-sonnet-test", LOAD_TEST_MODEL="claude-haiku-test")
    def test_load_test_uses_load_test_model(self):
        service, _ = _make_service(is_load_test=True)
        assert service.model == "claude-haiku-test"

    @override_settings(AGENT_MODEL="claude-sonnet-test", LOAD_TEST_MODEL="claude-haiku-test")
    def test_normal_mode_uses_agent_model(self):
        service, _ = _make_service(is_load_test=False)
        assert service.model == "claude-sonnet-test"

    def test_setup_braintrust_not_called_when_load_test(self):
        mock_setup = MagicMock(return_value=True)
        _make_service(is_load_test=True, mock_setup_fn=mock_setup)
        mock_setup.assert_not_called()

    def test_setup_braintrust_called_when_normal(self):
        mock_setup = MagicMock(return_value=True)
        _make_service(is_load_test=False, mock_setup_fn=mock_setup)
        mock_setup.assert_called_once()


# ---------------------------------------------------------------------------
# get_agent_service factory
# ---------------------------------------------------------------------------


class TestGetAgentServiceFactory:
    def test_default_passes_false(self):
        with patch("chat.V2.agent.claude_service.ClaudeAgentService") as MockSvc:
            from chat.V2.agent.claude_service import get_agent_service

            get_agent_service()
            MockSvc.assert_called_once_with(is_load_test=False)

    def test_passes_true(self):
        with patch("chat.V2.agent.claude_service.ClaudeAgentService") as MockSvc:
            from chat.V2.agent.claude_service import get_agent_service

            get_agent_service(is_load_test=True)
            MockSvc.assert_called_once_with(is_load_test=True)


# ---------------------------------------------------------------------------
# SDKOptionsBuilder — Braintrust key gating
# ---------------------------------------------------------------------------


class TestSDKOptionsBuilderBraintrustGating:
    """When braintrust_logging_enabled=False, keys must not appear in subprocess env."""

    def _make_builder(self, braintrust_logging_enabled: bool):
        from chat.V2.agent.sdk_options_builder import SDKOptionsBuilder

        mock_cls = MagicMock(spec=[])  # callable, no special attrs

        builder = SDKOptionsBuilder(
            options_cls=mock_cls,
            model="claude-test",
            max_tokens=1000,
            temperature=0.5,
            braintrust_api_key="real-bt-key",
            braintrust_project="my-project",
            braintrust_logging_enabled=braintrust_logging_enabled,
            mcp_server_name="sefaria",
        )
        return builder

    def test_braintrust_keys_absent_from_env_when_disabled(self):
        builder = self._make_builder(braintrust_logging_enabled=False)
        with patch.object(builder, "_supports_option", return_value=True):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "anthro-key"}):
                builder.build(system_prompt="test", mcp_server=MagicMock(), allowed_tools=[])
        env = builder.options_cls.call_args[1].get("env", {})
        assert "BRAINTRUST_API_KEY" not in env
        assert "BRAINTRUST_PROJECT" not in env
        assert "ANTHROPIC_API_KEY" in env

    def test_braintrust_keys_present_in_env_when_enabled(self):
        builder = self._make_builder(braintrust_logging_enabled=True)
        with patch.object(builder, "_supports_option", return_value=True):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "anthro-key"}):
                builder.build(system_prompt="test", mcp_server=MagicMock(), allowed_tools=[])
        env = builder.options_cls.call_args[1].get("env", {})
        assert env.get("BRAINTRUST_API_KEY") == "real-bt-key"
        assert env.get("BRAINTRUST_PROJECT") == "my-project"
