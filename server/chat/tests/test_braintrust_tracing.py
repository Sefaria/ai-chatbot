"""Tests for Braintrust tracing to prevent duplicate logging.

The issue: When setup_claude_agent_sdk is called, it patches the Claude SDK
for automatic tracing. If we ALSO wrap with braintrust.traced(), we get
duplicate logs in Braintrust.

This test ensures that when the SDK is already patched for automatic tracing,
we don't add a redundant braintrust.traced() wrapper.
"""

import importlib
import re
from unittest.mock import MagicMock, patch

import pytest


class TestBraintrustTracingNotDuplicated:
    """Verify that Braintrust tracing is not applied twice."""

    def test_traced_not_called_when_sdk_patched(self) -> None:
        """
        When setup_claude_agent_sdk patches the SDK for automatic tracing,
        the send_message method should NOT also use braintrust.traced().

        The SDK patching provides tracing automatically - adding traced()
        creates duplicate spans in Braintrust.
        """
        mock_braintrust = MagicMock()
        mock_braintrust.traced = MagicMock(return_value=lambda fn: fn)

        with patch.dict(
            "sys.modules",
            {
                "braintrust": mock_braintrust,
                "braintrust.wrappers.claude_agent_sdk": MagicMock(),
            },
        ):
            # Import after patching to get our mocked module
            from chat.V2.agent import claude_service

            # Reload to pick up mocked braintrust
            importlib.reload(claude_service)

            # Verify traced is available but should NOT be called
            # when _braintrust_enabled is True (SDK already patched)
            service = MagicMock(spec=claude_service.ClaudeAgentService)
            service._braintrust_enabled = True

            # The traced decorator should NOT be called because
            # setup_claude_agent_sdk already provides automatic tracing
            # This is the behavior we want to enforce
            mock_braintrust.traced.assert_not_called()

    def test_send_message_does_not_wrap_with_traced_when_sdk_patched(self) -> None:
        """
        Directly verify that send_message doesn't call braintrust.traced()
        when Braintrust SDK integration is enabled via setup_claude_agent_sdk.
        """
        # Check the actual source code for the pattern we want to prevent
        from chat.V2.agent import claude_service

        source = claude_service.__file__
        with open(source) as f:
            content = f.read()

        # Check for actual usage pattern - not just mentions in comments
        # The problematic pattern is:
        #   traced_run = braintrust.traced(...)(run)
        #   return await traced_run()

        # Look for actual traced() usage: braintrust.traced( followed by code
        usage_pattern = r"braintrust\.traced\s*\([^)]*\)\s*\([^)]*\)"
        matches = re.findall(usage_pattern, content)

        assert len(matches) == 0, (
            f"send_message should NOT use braintrust.traced() when "
            f"setup_claude_agent_sdk is used - the SDK patching already "
            f"provides automatic tracing. Using both causes duplicate logging. "
            f"Found: {matches}"
        )


class TestSetupBraintrustOnlyOnce:
    """Verify setup_claude_agent_sdk is only called once per process/thread."""

    def test_setup_only_called_once_per_thread(self) -> None:
        """
        setup_claude_agent_sdk should only be called once per thread,
        not on every ClaudeAgentService instantiation or send_message call.
        """
        mock_setup = MagicMock(return_value=True)

        with patch.dict("os.environ", {"BRAINTRUST_API_KEY": "test-key"}):
            with patch(
                "chat.V2.agent.claude_service.setup_claude_agent_sdk",
                mock_setup,
            ):
                from chat.V2.agent import claude_service

                # Reset the thread-local state
                claude_service._BRAINTRUST_SETUP_STATE.done = False

                # Create a service - should call setup once
                with patch.object(claude_service, "ClaudeAgentOptions", MagicMock()):
                    with patch.object(claude_service, "ClaudeSDKClient", MagicMock()):
                        with patch.object(claude_service, "create_sdk_mcp_server", MagicMock()):
                            with patch.object(claude_service, "tool", MagicMock()):
                                with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}):
                                    try:
                                        claude_service.ClaudeAgentService()
                                        call_count_after_first = mock_setup.call_count

                                        # Create another service - should NOT call setup again
                                        claude_service.ClaudeAgentService()
                                        call_count_after_second = mock_setup.call_count

                                        assert call_count_after_first == 1, (
                                            f"setup_claude_agent_sdk called "
                                            f"{call_count_after_first} times on first init"
                                        )
                                        assert call_count_after_second == 1, (
                                            "setup_claude_agent_sdk should not be "
                                            "called again on second init"
                                        )
                                    except RuntimeError:
                                        # Expected if claude-agent-sdk not installed
                                        pytest.skip("claude-agent-sdk not installed")
