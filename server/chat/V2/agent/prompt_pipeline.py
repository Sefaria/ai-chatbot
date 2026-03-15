"""Prompt assembly helpers for Claude agent turns."""

from __future__ import annotations

from dataclasses import dataclass

from ..prompts.prompt_fragments import build_prompt
from .contracts import ConversationMessage, MessageContext


@dataclass
class PromptBuildResult:
    conversation_text: str
    full_prompt: str
    summary_included: bool


def format_conversation(messages: list[ConversationMessage]) -> str:
    """Serialize conversation history into plain text for SDK prompting."""
    lines: list[str] = []
    for message in messages:
        role = "User" if message.role == "user" else "Assistant"
        lines.append(f"{role}: {message.content}")
    return "\n".join(lines)


def build_turn_prompt(
    *,
    messages: list[ConversationMessage],
    core_prompt: str,
    context: MessageContext,
) -> PromptBuildResult:
    """Build full prompt text (core prompt + summary/context + conversation)."""
    conversation_text = format_conversation(messages)
    full_prompt, summary_included = build_prompt(
        conversation_text,
        core_prompt=core_prompt,
        summary_text=context.summary_text,
        page_url=context.page_url,
    )
    return PromptBuildResult(
        conversation_text=conversation_text,
        full_prompt=full_prompt,
        summary_included=summary_included,
    )
