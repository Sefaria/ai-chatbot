"""
Claude Agent Service — public facade for the V2 Sefaria chatbot runtime.

The implementation is split into focused modules under chat/V2/agent:
- turn orchestration
- guardrail gate
- SDK option compatibility
- SDK execution loop
- tool runtime bridge
- trace logging and metrics mapping
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

import braintrust
from braintrust.wrappers.claude_agent_sdk import setup_claude_agent_sdk
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, create_sdk_mcp_server, tool
from claude_agent_sdk.types import AssistantMessage, ResultMessage

from ..prompts import PromptService, get_prompt_service
from ..utils import get_anthropic_client, get_braintrust_config, is_braintrust_tracing_enabled
from .contracts import AgentProgressUpdate, AgentResponse, ConversationMessage, MessageContext
from .guardrail_gate import DefaultGuardrailGate
from .sdk_options_builder import SDKOptionsBuilder
from .sdk_runner import ClaudeSDKRunner
from .sefaria_client import SefariaClient
from .tool_executor import SefariaToolExecutor
from .tool_runtime import ToolRuntime
from .trace_logger import BraintrustTraceLogger
from .tracing_runtime import ensure_braintrust_tracing
from .turn_orchestrator import TurnOrchestrator

logger = logging.getLogger("chat.agent")

# Must remain module-global so setup_claude_agent_sdk runs once per process.
_BRAINTRUST_SETUP_DONE = False


class ClaudeAgentService:
    """Public service entrypoint for running one Claude agent turn."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_iterations: int = 10,
        max_tokens: int = 8000,
        temperature: float = 0.7,
        prompt_service: PromptService | None = None,
    ):
        if (
            ClaudeAgentOptions is None
            or ClaudeSDKClient is None
            or create_sdk_mcp_server is None
            or tool is None
        ):
            raise RuntimeError(
                "claude-agent-sdk is required. Install with `pip install claude-agent-sdk`."
            )

        self.client = get_anthropic_client(api_key)
        self.prompt_service = prompt_service or get_prompt_service()
        bt = get_braintrust_config()
        self.braintrust_api_key = bt.api_key
        self.braintrust_project = bt.project

        api_key_str = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = api_key_str

        from django.conf import settings as django_settings

        self.model = model or django_settings.AGENT_MODEL
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.temperature = temperature

        self.sefaria_client = SefariaClient()
        self.tool_executor = SefariaToolExecutor(self.sefaria_client)
        self._mcp_server_name = "sefaria"

        self._setup_braintrust_tracing()

        tool_runtime = ToolRuntime(tool_executor=self.tool_executor, decorator_fn=tool)
        options_builder = SDKOptionsBuilder(
            options_cls=ClaudeAgentOptions,
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            braintrust_api_key=self.braintrust_api_key,
            braintrust_project=self.braintrust_project,
            mcp_server_name=self._mcp_server_name,
            logger=logger,
        )
        sdk_runner = ClaudeSDKRunner(
            client_cls=ClaudeSDKClient,
            assistant_message_cls=AssistantMessage,
            result_message_cls=ResultMessage,
        )
        guardrail_gate = DefaultGuardrailGate(logger=logger)
        trace_logger = BraintrustTraceLogger()
        self._orchestrator = TurnOrchestrator(
            model=self.model,
            mcp_server_name=self._mcp_server_name,
            prompt_service=self.prompt_service,
            create_mcp_server=create_sdk_mcp_server,
            tool_runtime=tool_runtime,
            options_builder=options_builder,
            sdk_runner=sdk_runner,
            guardrail_gate=guardrail_gate,
            trace_logger=trace_logger,
        )

    def _setup_braintrust_tracing(self) -> None:
        """Ensure Braintrust tracing is initialized for this process."""
        if not is_braintrust_tracing_enabled():
            return
        global _BRAINTRUST_SETUP_DONE
        _BRAINTRUST_SETUP_DONE = ensure_braintrust_tracing(
            project=self.braintrust_project,
            api_key=self.braintrust_api_key,
            setup_done=_BRAINTRUST_SETUP_DONE,
            setup_fn=setup_claude_agent_sdk,
        )

    async def send_message(
        self,
        messages: list[ConversationMessage],
        core_prompt_id: str | None = None,
        on_progress: Callable[[AgentProgressUpdate], None] | None = None,
        context: MessageContext | None = None,
    ) -> AgentResponse:
        """Run one chat turn and return the final response payload."""
        context = context or MessageContext()

        async def run() -> AgentResponse:
            return await self._orchestrator.run_turn(
                messages=messages,
                core_prompt_id=core_prompt_id,
                on_progress=on_progress,
                context=context,
            )

        if is_braintrust_tracing_enabled():
            run = braintrust.traced(name="chat-agent", type="task")(run)

        return await run()

    async def close(self) -> None:
        """Close the service and cleanup resources."""
        await self.sefaria_client.close()


def get_agent_service() -> ClaudeAgentService:
    """Create a fresh service instance (one per request)."""
    return ClaudeAgentService()
