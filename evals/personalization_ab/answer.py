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


def session_summary(turns: list[Turn], key: str) -> str:
    """The rolling summary production would hold after `turns` (one session, original answers).

    Replays the turns through the real SummaryService, as the view does after each turn
    (chat/V2/views.py), on a throwaway ChatSession that is deleted afterwards. `key` makes the
    session id unique (user, session, prompt)."""
    if not turns:
        return ""
    from chat.models import ChatSession
    from chat.V2.services.session_service import load_session_summary
    from chat.V2.summarization.summary_service import get_summary_service

    session = ChatSession.objects.create(session_id=f"ab-sum-{key}", user_id="ab")
    try:
        service = get_summary_service()
        for t in turns:
            service.update_summary(
                session=session,
                new_user_message=t.prompt,
                new_assistant_response=t.original_answer,
            )
        return load_session_summary(session)
    finally:
        session.delete()


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
    prompt: str,
    profile_text: str | None,
    summary_text: str | None,
    *,
    turn_number: int = 1,
    runner: Callable | None = None,
) -> Answer:
    """Run one turn the way production does: the conversation is only the new message, and the
    earlier turns of the session arrive as `MessageContext.summary_text`. Callers pass the same
    `summary_text` for the A and B runs of a prompt."""
    from chat.V2.agent.contracts import ConversationMessage, MessageContext

    context = MessageContext(
        session_id=f"ab-{user_id}",
        user_id=user_id,
        turn_number=turn_number,
        summary_text=summary_text or None,
        profile_text=profile_text or None,
        instructions_text=None,
    )
    messages = [ConversationMessage(role="user", content=prompt)]
    response = (runner or _default_runner)(messages, context)
    return Answer(text=response.content, trace_id=getattr(response, "trace_id", None))
