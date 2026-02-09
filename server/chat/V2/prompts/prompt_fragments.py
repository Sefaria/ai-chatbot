"""
Reusable text fragments that are composed into LLM prompts.

All text that gets injected into messages sent to Claude lives here,
so prompt content is easy to find, review, and update in one place.

Note: The core system prompt lives in Braintrust (fetched by prompt_service.py).
The summary generation prompt lives in summarization/summary_service.py
since it targets a different model.
"""

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
    parts = [core_prompt]
    summary_included = False

    if summary_text:
        parts.append(CONVERSATION_SUMMARY_SECTION.format(summary_text=summary_text))
        summary_included = True
    if page_url:
        parts.append(PAGE_CONTEXT_SECTION.format(page_url=page_url))

    return SECTION_SEPARATOR.join(parts), summary_included
