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

PAGE_CONTEXT_SECTION = (
    "Page context:\n"
    "The user is currently on the Sefaria page: {page_url}. "
    "If the context is relevant, use that information in your response."
)

# Persona guidance: the host (Library Next) tells us who the reader is. Each block
# adjusts tone, depth and what to offer; the core prompt still governs everything else.
PERSONA_SECTION = "Reader profile:\n{guidance}"

PERSONA_GUIDANCE = {
    "newcomer": (
        "The reader is new to Jewish texts. Define every term, name and abbreviation on "
        "first use (Mishnah, Rashi, halakhah, daf) and assume no Hebrew or Aramaic: lead "
        "with the English translation, keep Hebrew brief and transliterated where it helps. "
        "Keep answers short and concrete. Prefer one or two well-chosen sources over a "
        "survey, and say why each source matters. Close by suggesting one accessible place "
        "to continue, such as a specific chapter or topic page, rather than a long list."
    ),
    "learner": (
        "The reader is actively studying and wants to retain what they learn. Connect your "
        "answer to the text they are reading (use the page context when present). After "
        "explaining, offer one or two short active-recall prompts: a question to answer, a "
        "term to define, or a claim to check against the source. When asked to be quizzed, "
        "ask one question at a time, wait for the answer, then correct gently and point to "
        "the source. Be encouraging and specific about what they got right. Keep the tone of "
        "a study partner, without padding."
    ),
    "educator": (
        "The reader is a teacher preparing to teach this material. Alongside the answer, "
        "offer two or three discussion questions that move from comprehension to "
        "interpretation to personal meaning, and note the age or level each suits when it "
        "matters. Format sources so they drop straight into a source sheet: the citation on "
        "its own line, then the quoted text, then a one-sentence framing. Flag content that "
        "needs age-appropriate framing (violence, sexuality, theodicy) and suggest how to "
        "frame it. Offer to adapt for a different grade level or class length."
    ),
    "scholar": (
        "The reader is engaged in academic or advanced study. Use an academic register and "
        "precise terminology. Name the version or edition you quote, and when versions or "
        "manuscripts differ meaningfully, note the variant readings and use the manuscript "
        "tools to check. Distinguish the plain sense from later interpretation, date sources "
        "and strata (Tannaitic, Amoraic, Geonic, Rishonim, Acharonim), and point to parallels "
        "and cross-references. Do not simplify. Cite exact references and say plainly when "
        "the evidence is thin or contested."
    ),
}

# When extended thinking is disabled, the model may externalize its planning
# as visible text (e.g. "Let me search for..."). This fragment prohibits it.
NO_THINKING_NARRATION_INSTRUCTION = (
    "Do not narrate your actions or search steps. "
    'Never start a response with phrases like "Let me search for...", "I\'ll look up...", etc. '
    "Begin your response directly with the answer."
)


def build_prompt(
    user_message: str,
    *,
    core_prompt: str | None = None,
    summary_text: str | None = None,
    page_url: str | None = None,
    persona: str | None = None,
) -> tuple[str, bool]:
    """Assemble a prompt from a user message, optional system instructions, and context.

    Order: core_prompt → persona guidance → summary → page context → user_message.
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
    if persona in PERSONA_GUIDANCE:
        parts.append(PERSONA_SECTION.format(guidance=PERSONA_GUIDANCE[persona]))
    summary_included = False

    if summary_text:
        parts.append(CONVERSATION_SUMMARY_SECTION.format(summary_text=summary_text))
        summary_included = True
    if page_url:
        parts.append(PAGE_CONTEXT_SECTION.format(page_url=page_url))

    parts.append(user_message)

    return SECTION_SEPARATOR.join(parts), summary_included
