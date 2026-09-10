"""Running one turn with Claude Code against the Sefaria MCP server.

This is the whole agent. There is no orchestration layer because Claude Code is
the orchestrator: it decides which MCP tools to call, loops until it has an
answer, and keeps the conversation history for the session.

Two properties are load-bearing and easy to lose:

* **Sefaria tools only.** ``bypassPermissions`` would otherwise hand the agent
  the built-in file and shell tools. The allowlist is what keeps this to the
  same surface the hosted agent has — no filesystem access, by design.
* **One session per conversation.** The SDK client holds the history, which is
  why no summarisation is needed here at all.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, UserMessage

from . import config

logger = logging.getLogger("local_agent")

# Allows every tool from our MCP server and nothing else. Claude Code reads a
# bare "mcp__<server>" as "all tools from that server", so this keeps working as
# the MCP server gains tools, without the daemon knowing their names.
SEFARIA_TOOLS = f"mcp__{config.MCP_SERVER_NAME}"


@dataclass
class TurnResult:
    """What a finished turn produced, for the client and for history."""

    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    # tool_use_id -> display name, so a result can be paired with its call.
    pending_tools: dict[str, str] = field(default_factory=dict)


def build_options(system_prompt: str) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=config.model(),
        system_prompt=system_prompt,
        mcp_servers={config.MCP_SERVER_NAME: config.mcp_server_config()},
        # Without this the agent also gets Read, Write, Bash and the rest.
        allowed_tools=[SEFARIA_TOOLS],
        permission_mode="bypassPermissions",
        # Only our MCP server: never whatever the user has configured locally.
        strict_mcp_config=True,
    )


class Conversation:
    """One chat session, holding the SDK client that carries its history."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        # Filled in on the first turn, once we have a user token to fetch
        # Sefaria's prompt with. The session keeps it for its whole life, so a
        # mid-conversation prompt change cannot alter an ongoing chat.
        self.system_prompt: str | None = None
        self._client: ClaudeSDKClient | None = None
        self.last_result: TurnResult | None = None

    async def _connected(self) -> ClaudeSDKClient:
        if self._client is None:
            options = build_options(self.system_prompt or "")
            self._client = ClaudeSDKClient(options=options)
            await self._client.connect()
            logger.info("session %s: connected to Claude", self.session_id)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    async def ask(self, text: str) -> AsyncIterator[tuple[str, dict]]:
        """Run one turn, yielding (event_name, payload) as it goes.

        Events match what the widget already renders, so the UI needs no
        knowledge of which agent produced them: ``status`` while working,
        ``tool_start`` / ``tool_end`` around each tool call, and one final
        ``complete`` carrying the answer.
        """
        client = await self._connected()
        await client.query(text)

        result = TurnResult()

        async for message in client.receive_response():
            for event in _events_for(message, result):
                yield event

        self.last_result = result
        yield ("complete", {"type": "complete", "text": result.text})


def _events_for(message, result: TurnResult) -> list[tuple[str, dict]]:
    """Translate one SDK message into widget events, updating the result."""
    events: list[tuple[str, dict]] = []

    if isinstance(message, ResultMessage):
        usage = getattr(message, "usage", None) or {}
        result.input_tokens = usage.get("input_tokens")
        result.output_tokens = usage.get("output_tokens")
        return events

    if isinstance(message, UserMessage):
        # Tool results come back as user-role blocks. Without a tool_end the
        # widget leaves the tool showing as still running for the rest of the
        # turn, so every tool_start needs its pair.
        for block in getattr(message, "content", None) or []:
            tool_use_id = getattr(block, "tool_use_id", None)
            if not tool_use_id:
                continue
            events.append(
                (
                    "progress",
                    {
                        "type": "tool_end",
                        "tool_name": result.pending_tools.pop(tool_use_id, ""),
                        "is_error": bool(getattr(block, "is_error", False)),
                    },
                )
            )
        return events

    if not isinstance(message, AssistantMessage):
        return events

    result.model = getattr(message, "model", None) or result.model

    for block in getattr(message, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            result.text += text
            continue

        name = getattr(block, "name", None)
        if not name:
            continue

        # Tool names arrive prefixed (mcp__sefaria__get_text); the widget shows
        # them to a reader, so strip the wiring.
        friendly = name.rsplit("__", 1)[-1]
        tool_input = getattr(block, "input", None) or {}
        result.tool_calls.append({"tool_name": friendly, "tool_input": tool_input})
        block_id = getattr(block, "id", None)
        if block_id:
            result.pending_tools[block_id] = friendly
        events.append(
            ("progress", {"type": "tool_start", "tool_name": friendly, "tool_input": tool_input})
        )

    return events


class Conversations:
    """The live sessions, one SDK client each.

    Held in memory because a conversation only means anything while its client
    is alive; a restarted daemon starts fresh, and the browser still has the
    transcript.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Conversation] = {}

    def get(self, session_id: str) -> Conversation:
        if session_id not in self._sessions:
            self._sessions[session_id] = Conversation(session_id)
        return self._sessions[session_id]

    async def drop(self, session_id: str) -> None:
        conversation = self._sessions.pop(session_id, None)
        if conversation is not None:
            await conversation.close()

    async def close_all(self) -> None:
        for session_id in list(self._sessions):
            await self.drop(session_id)
