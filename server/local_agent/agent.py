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

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher
from claude_agent_sdk.types import (
    AssistantMessage,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    ToolPermissionContext,
    UserMessage,
)

from . import config

logger = logging.getLogger("local_agent")

# Every tool from our MCP server carries this prefix. Nothing else may run.
SEFARIA_TOOL_PREFIX = f"mcp__{config.MCP_SERVER_NAME}__"


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


async def only_sefaria_tools(
    tool_name: str,
    tool_input: dict,
    context: ToolPermissionContext,
) -> PermissionResult:
    """Allow the Sefaria library tools; refuse everything else.

    This is the control, not ``allowed_tools``. That list is advisory and
    ``permission_mode="bypassPermissions"`` skips it entirely — with both set,
    the agent still ran Bash and Read against the user's machine. Deciding here
    means the default is refusal, so a built-in tool added by a future CLI
    release is denied without anyone having to notice it exists.
    """
    if tool_name.startswith(SEFARIA_TOOL_PREFIX):
        return PermissionResultAllow()

    logger.warning("blocked a non-Sefaria tool: %s", tool_name)
    return PermissionResultDeny(
        message=(
            "Only the Sefaria library tools are available here. "
            "Answer using those, or say what you could not look up."
        )
    )


# Named explicitly as a third layer. Not the control — the hook below refuses
# anything unlisted — but it keeps the obvious dangers out even if a future SDK
# changed how hooks and settings interact.
BUILT_IN_TOOLS = [
    "Bash",
    "BashOutput",
    "KillShell",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "Glob",
    "Grep",
    "LS",
    "WebFetch",
    "WebSearch",
    "Task",
    "TodoWrite",
    "ToolSearch",
]


async def refuse_non_sefaria_tools(hook_input, tool_use_id, context) -> dict:
    """PreToolUse hook: the control that actually holds.

    Hooks run for every tool call, before any allow rule is consulted. That is
    why this and not ``can_use_tool``: allow rules — including ones in the
    user's own ~/.claude/settings.json — approve a call *before* the callback
    runs. Verified the hard way: with only the callback in place the agent
    still ran Bash and Read, because the user's settings allowed them.
    """
    tool_name = (hook_input or {}).get("tool_name", "")
    if tool_name.startswith(SEFARIA_TOOL_PREFIX):
        return {}

    logger.warning("blocked a non-Sefaria tool: %s", tool_name)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Only the Sefaria library tools are available here. "
                "Answer using those, or say what you could not look up."
            ),
        }
    }


def build_options(system_prompt: str) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=config.model(),
        system_prompt=system_prompt,
        mcp_servers={config.MCP_SERVER_NAME: config.mcp_server_config()},
        # Root cause of the escape: without this the CLI loads the user's own
        # settings files, and their permission allow rules pre-approve Bash and
        # Read. The agent must not inherit how its host uses Claude Code.
        setting_sources=[],
        # The control. Runs before any allow rule, for every call.
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[refuse_non_sefaria_tools])]},
        # Further layers of the same intent; neither is relied on alone.
        can_use_tool=only_sefaria_tools,
        disallowed_tools=BUILT_IN_TOOLS,
        allowed_tools=[SEFARIA_TOOL_PREFIX.rstrip("_")],
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
        # Reported to the widget, which uses it for the per-conversation limit.
        self.turn_count = 0

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
        logger.info("turn %s: %s", self.session_id, summarise(result))
        yield ("complete", {"type": "complete", "text": result.text})


def tool_input_of(block) -> dict:
    return getattr(block, "input", None) or {}


def _brief(tool_input: dict, limit: int = 90) -> str:
    """A one-line rendering of a tool's arguments, for the log."""
    if not tool_input:
        return ""
    rendered = ", ".join(f"{key}={value!r}" for key, value in tool_input.items())
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def summarise(result: TurnResult) -> str:
    """One line saying which tools a finished turn actually used.

    The interesting case is the empty one: no tools means the model answered
    from its own knowledge rather than from the Sefaria library, which looks
    identical to a good answer until you check.
    """
    if not result.tool_calls:
        return "no tools — answered from the model's own knowledge"

    names = [call.get("raw_name") or call["tool_name"] for call in result.tool_calls]
    sefaria = sum(1 for name in names if name.startswith(f"mcp__{config.MCP_SERVER_NAME}__"))
    other = len(names) - sefaria
    summary = f"{len(names)} tool calls ({sefaria} via the Sefaria MCP"
    if other:
        summary += f", {other} NOT from the MCP"
    return summary + "): " + ", ".join(names)


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

        # Logged with the prefix intact: "mcp__sefaria__get_text" is the proof
        # the call went to our MCP server rather than a built-in tool, and that
        # distinction is the whole question when checking the agent is really
        # using the Sefaria library.
        logger.info("  tool  %s %s", name, _brief(tool_input_of(block)))

        # The widget shows this to a reader, so strip the wiring there.
        friendly = name.rsplit("__", 1)[-1]
        tool_input = tool_input_of(block)
        result.tool_calls.append(
            {"tool_name": friendly, "tool_input": tool_input, "raw_name": name}
        )
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
