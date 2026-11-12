"""
Reusable text fragments for prompts and user-facing messages.

All static text — whether injected into LLM prompts or sent back to the
user — lives here so content is easy to find, review, and update in one place.

Note: The core system prompt lives in Braintrust (fetched by prompt_service.py).
The summary generation prompt lives in summarization/summary_service.py
since it targets a different model.
"""

from ..personalization.parsing import escape_block
from .chapters import load_chapter

# ---------------------------------------------------------------------------
# Messages sent back to the user
# ---------------------------------------------------------------------------
# Centralized here so copy is easy to review, update, and translate.
# ERROR_FALLBACK_MESSAGE — shown when the agent crashes (DB-persisted as the response)
# INTERNAL_ERROR_MESSAGE — sent to the client via SSE/API error events (less detail)
# GUARDRAIL_REJECTION_FALLBACK — fallback when guardrail can't run (service down, malformed response)

ERROR_FALLBACK_MESSAGE = "I'm sorry, I encountered an error processing your request."

INTERNAL_ERROR_MESSAGE = "An internal error occurred."

GUARDRAIL_REJECTION_FALLBACK = (
    "I'm sorry, I wasn't able to process your message. Please try again in a moment."
)

# Internal reasons logged when the guardrail can't run or returns bad data.
# Not user-facing — used in GuardrailResult.reason for debugging / tracing.
GUARDRAIL_UNAVAILABLE_REASON = "Guardrail service unavailable"
GUARDRAIL_MALFORMED_REASON = "Malformed guardrail response"

# ---------------------------------------------------------------------------
# Prompt fragments (injected into LLM system prompts)
# ---------------------------------------------------------------------------

SECTION_SEPARATOR = "\n\n"

CONVERSATION_SUMMARY_SECTION = "Conversation summary:\n{summary_text}"

USER_INSTRUCTIONS_SECTION = "<user_instructions>\n{text}\n</user_instructions>"

USER_PROFILE_SECTION = "<user_profile>\n{text}\n</user_profile>"

PAGE_CONTEXT_SECTION = (
    "Page context:\n"
    "The user is currently on the Sefaria page: {page_url}. "
    "If the context is relevant, use that information in your response."
)


def build_prompt(
    user_message: str,
    *,
    core_prompt: str | None = None,
    summary_text: str | None = None,
    page_url: str | None = None,
    instructions_text: str | None = None,
    profile_text: str | None = None,
) -> tuple[str, bool]:
    """Assemble a prompt from a user message, optional system instructions, and context.

    Order: core_prompt → personalization chapter → user instructions → user profile → summary →
    page context → user_message. The chapter appears only when at least one block does.
    This follows Anthropic's long-context guidance: place long reference material
    (system instructions, context) first, and the query last — closest to where
    the model generates its response — to maximize instruction recall.

    Used for both the agent system prompt and the guardrail input (which omits
    core_prompt).

    Returns (prompt_text, summary_included).
    """
    if core_prompt is not None and not core_prompt.strip():
        raise ValueError("core_prompt cannot be empty")

    parts: list[str] = []
    if core_prompt is not None:
        parts.append(core_prompt)
    summary_included = False

    # Whatever the caller passes, the texts can never close or open a tag.
    blocks = [
        USER_INSTRUCTIONS_SECTION.format(text=text)
        for text in [escape_block(instructions_text or "")]
        if text
    ] + [
        USER_PROFILE_SECTION.format(text=text)
        for text in [escape_block(profile_text or "")]
        if text
    ]
    if blocks:
        parts.append(load_chapter("personalization_answering"))
        parts.extend(blocks)
    if summary_text:
        parts.append(CONVERSATION_SUMMARY_SECTION.format(summary_text=summary_text))
        summary_included = True
    if page_url:
        parts.append(PAGE_CONTEXT_SECTION.format(page_url=page_url))

    parts.append(user_message)

    return SECTION_SEPARATOR.join(parts), summary_included
