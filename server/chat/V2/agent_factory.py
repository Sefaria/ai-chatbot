"""Django host wiring for the agent runtime.

The agent package under ``chat/V2/agent`` takes an :class:`AgentConfig` and reads
no Django settings of its own, so it can run under other hosts. This module is
the Django host's half of that contract: it reads settings and builds the config.
"""

from __future__ import annotations

from django.conf import settings

from .agent import AgentConfig, ClaudeAgentService


def build_agent_config(is_load_test: bool = False) -> AgentConfig:
    """Build the agent config for one request from Django settings.

    Load-test turns run a cheaper model and skip Braintrust entirely, so the flag
    resolves here rather than inside the agent.
    """
    return AgentConfig(
        model=settings.LOAD_TEST_MODEL if is_load_test else settings.AGENT_MODEL,
        response_format_prompt_slug=settings.RESPONSE_FORMAT_PROMPT_SLUG,
        braintrust_logging_enabled=settings.BRAINTRUST_LOGGING_ENABLED and not is_load_test,
    )


def get_agent_service(is_load_test: bool = False) -> ClaudeAgentService:
    """Create a fresh service instance (one per request)."""
    return ClaudeAgentService(config=build_agent_config(is_load_test))
