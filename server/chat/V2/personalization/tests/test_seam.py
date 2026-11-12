"""The prompt seam: byte-identical with no profile, correct placement with one."""

import itertools
from unittest.mock import MagicMock

import pytest

from chat.V2.agent.contracts import ConversationMessage, MessageContext
from chat.V2.agent.prompt_pipeline import build_turn_prompt
from chat.V2.agent.trace_logger import BraintrustTraceLogger
from chat.V2.prompts.prompt_fragments import (
    CONVERSATION_SUMMARY_SECTION,
    PAGE_CONTEXT_SECTION,
    SECTION_SEPARATOR,
    build_prompt,
)


def build_prompt_before_the_seam(
    user_message, *, core_prompt=None, summary_text=None, page_url=None
):
    """The function as it was before profile_text existed (reference for byte-identity)."""
    if core_prompt is not None and not core_prompt.strip():
        raise ValueError("core_prompt cannot be empty")
    parts = []
    if core_prompt is not None:
        parts.append(core_prompt)
    summary_included = False
    if summary_text:
        parts.append(CONVERSATION_SUMMARY_SECTION.format(summary_text=summary_text))
        summary_included = True
    if page_url:
        parts.append(PAGE_CONTEXT_SECTION.format(page_url=page_url))
    parts.append(user_message)
    return SECTION_SEPARATOR.join(parts), summary_included


@pytest.mark.parametrize(
    "core,summary,page",
    list(
        itertools.product(
            [None, "You are the Library Assistant."],
            [None, "", "They studied Berakhot."],
            [None, "https://www.sefaria.org/Genesis.1"],
        )
    ),
)
@pytest.mark.parametrize("profile", [None, ""])
def test_prompt_is_byte_identical_without_a_profile(core, summary, page, profile):
    kwargs = {"core_prompt": core, "summary_text": summary, "page_url": page}
    expected = build_prompt_before_the_seam("What is a sugya?", **kwargs)
    assert build_prompt("What is a sugya?", profile_text=profile, **kwargs) == expected
    assert build_prompt("What is a sugya?", **kwargs) == expected


def test_turn_prompt_is_identical_with_default_context():
    context = MessageContext(summary_text="S", page_url="https://x.org")
    messages = [ConversationMessage("user", "hi")]
    result = build_turn_prompt(messages=messages, core_prompt="CORE", context=context)
    expected, _ = build_prompt_before_the_seam(
        "User: hi", core_prompt="CORE", summary_text="S", page_url="https://x.org"
    )
    assert result.full_prompt == expected


def test_message_context_defaults():
    context = MessageContext()
    assert context.profile_text is None and context.instructions_text is None


def _logged_metadata(context):
    span = MagicMock()
    BraintrustTraceLogger().log_input(bt_span=span, user_message="hi", context=context, model="m")
    return span.log.call_args.kwargs["metadata"]


def test_trace_metadata_marks_personalized_turns():
    metadata = _logged_metadata(MessageContext(profile_text="- A: b"))
    assert metadata["personalized"] is True
    assert _logged_metadata(MessageContext(instructions_text="Be brief."))["personalized"] is True


def test_trace_metadata_unchanged_without_profile():
    metadata = _logged_metadata(MessageContext())
    assert "personalized" not in metadata


def test_the_gate_is_off_by_default():
    from django.conf import settings

    assert settings.PERSONALIZATION_OVERRIDE_ENABLED is False
    assert settings.PERSONALIZATION_JEV_GATE == "off"
