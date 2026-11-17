"""Tests for SDKOptionsBuilder Braintrust key gating.

When braintrust logging is disabled, the Braintrust keys must not reach the
subprocess environment, and include_partial_messages follows option support.
"""

from unittest.mock import MagicMock, patch


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

    def test_partial_messages_enabled_when_supported(self):
        builder = self._make_builder(braintrust_logging_enabled=True)
        with patch.object(builder, "_supports_option", return_value=True):
            builder.build(system_prompt="test", mcp_server=MagicMock(), allowed_tools=[])

        assert builder.options_cls.call_args[1]["include_partial_messages"] is True

    def test_partial_messages_omitted_when_unsupported(self):
        builder = self._make_builder(braintrust_logging_enabled=True)
        with patch.object(
            builder,
            "_supports_option",
            side_effect=lambda option: option != "include_partial_messages",
        ):
            builder.build(system_prompt="test", mcp_server=MagicMock(), allowed_tools=[])

        assert "include_partial_messages" not in builder.options_cls.call_args[1]
