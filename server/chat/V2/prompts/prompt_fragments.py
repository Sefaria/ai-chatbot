"""
Reusable text fragments that are composed into LLM prompts.

All text that gets injected into messages sent to Claude lives here,
so prompt content is easy to find, review, and update in one place.

Note: The core system prompt lives in Braintrust (fetched by prompt_service.py).
"""

SECTION_SEPARATOR = "\n\n"

PAGE_CONTEXT_SECTION = (
    "The user is currently on the Sefaria page: {page_url}. "
    "If the context is relevant, use that information in your response."
)


def build_system_prompt(
    core_prompt: str,
    *,
    page_url: str | None = None,
) -> str:
    """Compose the full system prompt from core prompt and optional context sections."""
    if not core_prompt or not core_prompt.strip():
        raise ValueError("core_prompt cannot be empty")

    parts = [core_prompt]

    if page_url:
        parts.append(PAGE_CONTEXT_SECTION.format(page_url=page_url))

    return SECTION_SEPARATOR.join(parts)
