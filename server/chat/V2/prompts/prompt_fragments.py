"""
Reusable text fragments for prompts and user-facing messages.

All static text — whether injected into LLM prompts or sent back to the
user — lives here so content is easy to find, review, and update in one place.

Note: The core system prompt lives in Braintrust (fetched by prompt_service.py).
The summary generation prompt lives in summarization/summary_service.py
since it targets a different model.
"""

# ---------------------------------------------------------------------------
# Messages sent back to the user
# ---------------------------------------------------------------------------

ERROR_FALLBACK_MESSAGE = "I'm sorry, I encountered an error processing your request."

INTERNAL_ERROR_MESSAGE = "An internal error occurred."

GUARDRAIL_REJECTION_MESSAGE = (
    "I can only help with questions related to Jewish texts and Torah encyclopaedia available on Sefaria. "
    "Could you rephrase your question to be about a Jewish text or topic?"
)

# ---------------------------------------------------------------------------
# Prompt fragments (injected into LLM system prompts)
# ---------------------------------------------------------------------------

SECTION_SEPARATOR = "\n\n"

CONVERSATION_SUMMARY_SECTION = "Conversation summary:\n{summary_text}"

PAGE_CONTEXT_SECTION = (
    "The user is currently on the Sefaria page: {page_url}. "
    "If the context is relevant, use that information in your response."
)


def build_system_prompt(
    core_prompt: str,
    *,
    summary_text: str | None = None,
    page_url: str | None = None,
) -> tuple[str, bool]:
    """Compose the full system prompt from core prompt and optional context sections.

    Returns (system_prompt, summary_included).
    """
    if not core_prompt or not core_prompt.strip():
        raise ValueError("core_prompt cannot be empty")

    parts = [core_prompt]
    summary_included = False

    if summary_text:
        parts.append(CONVERSATION_SUMMARY_SECTION.format(summary_text=summary_text))
        summary_included = True
    if page_url:
        parts.append(PAGE_CONTEXT_SECTION.format(page_url=page_url))

    return SECTION_SEPARATOR.join(parts), summary_included
