"""Tests for the CLAUDE_SDK_DEBUG switch in SDKOptionsBuilder.

Covers:
- unset variable keeps debug output on (prior default)
- explicit falsy strings turn debug output off
- explicit truthy strings keep debug output on
"""

import pytest

from chat.V2.agent.sdk_options_builder import SDKOptionsBuilder


def _capturing_options_cls(**kwargs):
    """Stand-in for ClaudeAgentOptions that records the kwargs it receives."""
    return kwargs


def _build_options(monkeypatch, debug_value):
    if debug_value is None:
        monkeypatch.delenv("CLAUDE_SDK_DEBUG", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_SDK_DEBUG", debug_value)

    builder = SDKOptionsBuilder(
        options_cls=_capturing_options_cls,
        model="claude-test",
        max_tokens=1000,
        temperature=0.5,
        braintrust_api_key="bt-key",
        braintrust_project="bt-project",
        mcp_server_name="sefaria",
    )
    options, _ = builder.build(system_prompt="test", mcp_server=object(), allowed_tools=[])
    return options


class TestSDKDebugSwitch:
    def test_unset_keeps_debug_enabled(self, monkeypatch):
        options = _build_options(monkeypatch, None)
        assert options.get("extra_args") == {"debug-to-stderr": None}
        assert "stderr" in options

    @pytest.mark.parametrize("value", ["0", "false", "FALSE", " 0 ", "no", "off"])
    def test_falsy_values_disable_debug(self, monkeypatch, value):
        options = _build_options(monkeypatch, value)
        assert "extra_args" not in options
        assert "stderr" not in options

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values_enable_debug(self, monkeypatch, value):
        options = _build_options(monkeypatch, value)
        assert options.get("extra_args") == {"debug-to-stderr": None}
        assert "stderr" in options
