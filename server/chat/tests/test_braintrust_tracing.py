"""Tests for Braintrust tracing to prevent duplicate SDK patching.

The issue: setup_claude_agent_sdk patches SDK classes globally, but if tracked
with a thread-local flag, each new thread would re-patch, stacking wrappers
and creating duplicate spans for every LLM call.

This test ensures setup is only called once per process (global flag).
"""

from unittest.mock import MagicMock, patch

import pytest


class TestSetupBraintrustOnlyOnce:
    """Verify setup_claude_agent_sdk is only called once per process."""

    def test_setup_only_called_once_globally(self) -> None:
        """
        setup_claude_agent_sdk should only be called once globally (not per thread),
        because it patches SDK classes globally. Multiple calls would wrap the SDK
        multiple times, causing deeply nested spans in Braintrust.
        """
        mock_setup = MagicMock(return_value=True)

        with patch.dict("os.environ", {"BRAINTRUST_API_KEY": "test-key"}):
            with patch(
                "chat.V2.agent.claude_service.setup_claude_agent_sdk",
                mock_setup,
            ):
                from chat.V2.agent import claude_service

                # Reset the global state
                claude_service._BRAINTRUST_SETUP_DONE = False

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

    def test_global_flag_prevents_rewrapping(self) -> None:
        """
        Verify the global flag is used (not thread-local) to prevent
        multiple threads from re-patching the SDK.
        """
        from chat.V2.agent import claude_service

        # Check that _BRAINTRUST_SETUP_DONE is a module-level variable (not threading.local)
        assert hasattr(claude_service, "_BRAINTRUST_SETUP_DONE")
        assert isinstance(claude_service._BRAINTRUST_SETUP_DONE, bool)
