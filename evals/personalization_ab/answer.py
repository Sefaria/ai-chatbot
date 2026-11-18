"""Answer one prompt with the real agent pipeline, in process, under a given Profile.

The profile goes in through `MessageContext.profile_text`, the same field the production view
fills from `personalization_runtime.context_for_request` (chat/V2/views.py). `instructions_text`
stays empty: the comparison is about the Profile only.

Must run inside a Django process (the agent reads settings). Real calls need ANTHROPIC_API_KEY
and BRAINTRUST_API_KEY like the server does.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from .select import Turn


@dataclass
class Answer:
    text: str
    trace_id: str | None


def build_messages(history: list[Turn], prompt: str):
    from chat.V2.agent.contracts import ConversationMessage

    messages: list = []
    for t in history:
        messages.append(ConversationMessage(role="user", content=t.prompt))
        messages.append(
            ConversationMessage(role="assistant", content=t.original_answer)
        )
    messages.append(ConversationMessage(role="user", content=prompt))
    return messages


def _default_runner(messages, context):
    from chat.V2.agent.claude_service import get_agent_service
    from django.conf import settings

    agent = get_agent_service()

    async def go():
        try:
            return await agent.send_message(
                messages=messages,
                core_prompt_id=settings.CORE_PROMPT_SLUG,
                context=context,
            )
        finally:
            await agent.close()

    return asyncio.run(go())


def answer_turn(
    user_id: str,
    history: list[Turn],
    prompt: str,
    profile_text: str | None,
    *,
    runner: Callable | None = None,
) -> Answer:
    """Run one turn. `history` becomes alternating user/assistant messages before `prompt`;
    callers pass the same `history` for the original, A and B runs of a prompt."""
    from chat.V2.agent.contracts import MessageContext

    context = MessageContext(
        session_id=f"ab-{user_id}",
        user_id=user_id,
        turn_number=len(history) + 1,
        profile_text=profile_text or None,
        instructions_text=None,
    )
    response = (runner or _default_runner)(build_messages(history, prompt), context)
    return Answer(text=response.content, trace_id=getattr(response, "trace_id", None))
