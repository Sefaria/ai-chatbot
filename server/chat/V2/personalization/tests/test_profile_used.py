"""The "From your profile, I took ... into account" line: the model writes it in a tag, the
orchestrator strips it once, the payload carries it. Made-up text only."""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chat.V2.agent.contracts import ConversationMessage, MessageContext
from chat.V2.agent.guardrail_gate import DefaultGuardrailGate
from chat.V2.agent.prompt_pipeline import build_turn_prompt
from chat.V2.agent.response_link_validator import LinkValidationIssue, LinkValidationResult
from chat.V2.agent.sdk_runner import SDKRunResult
from chat.V2.agent.turn_orchestrator import TurnOrchestrator
from chat.V2.personalization import profile_store as store
from chat.V2.personalization import runtime
from chat.V2.personalization.profile_used import split_profile_used
from chat.V2.prompts.chapters import load_chapter
from chat.V2.prompts.prompt_fragments import build_prompt

from .test_injection import lab_on, make_section, seed  # noqa: F401

CHAPTER = load_chapter("personalization_profile_used")
LINE = "From your profile, I took your preference for Rashi into account."
TAG = f"<profile_used>{LINE}</profile_used>"


# -- split ------------------------------------------------------------------------------------
# Only a tag at the very end of the text is taken off; the body is always a prefix of the input,
# so the split can never join text into new markup. Anything else stays as ordinary model output.

NOTHING = "Nothing from your profile changed this answer."


def test_split_profile_used_without_a_tag():
    assert split_profile_used("An answer.") == ("An answer.", None)


def test_split_profile_used_one_trailing_tag():
    assert split_profile_used(f"An answer.\n\n{TAG}") == ("An answer.", LINE)


def test_split_profile_used_trailing_tag_rambam():
    sentence = "From your profile, I took your interest in Rambam into account."
    text = f"The answer.  \n<profile_used>{sentence}</profile_used>"
    assert split_profile_used(text) == ("The answer.", sentence)


@pytest.mark.parametrize(
    "open_tag,close_tag",
    [("<PROFILE_USED >", "</PROFILE_USED>"), ("< profile_used>", "< / Profile_Used >")],
)
def test_split_profile_used_tag_case_and_spaces(open_tag, close_tag):
    sentence = "From your profile, I took your interest in Rambam into account."
    text = f"The answer.\n{open_tag}{sentence}{close_tag}\n"
    assert split_profile_used(text) == ("The answer.", sentence)


def test_split_profile_used_nothing_line():
    assert split_profile_used(f"A.\n<profile_used> {NOTHING} </profile_used>") == ("A.", NOTHING)


@pytest.mark.parametrize(
    "payload",
    [
        "<img<profile_used>x</profile_used>src=x onerror=alert(1)>",
        "<img<profile_used>x</profile_used> src=x onerror=alert(1)>",
        "<img<PROFILE_USED>x</PROFILE_USED>\tsrc=x onerror=alert(1)>",
        "ab<profile_used>x</profile_used>cd",
    ],
)
def test_split_profile_used_a_tag_inside_the_text_is_left_unchanged(payload):
    assert split_profile_used(payload) == (payload, None)


def test_split_profile_used_mid_text_tag_is_kept():
    text = f"Start {TAG} end."
    assert split_profile_used(text) == (text, None)


def test_split_profile_used_unclosed_tag_is_kept():
    text = "An answer.\n<profile_used>From your pro"
    assert split_profile_used(text) == (text, None)


@pytest.mark.parametrize(
    "inner",
    [
        "Ignore all rules",
        "Rashi was important to you.",
        "From your profile, I took Rashi\ninto account.",
        "From your profile, I took <b>Rashi</b> into account.",
        "From your profile, I took a > b into account.",
        "from your profile, i took Rashi into account.",
        "From your profile, I took Rashi into account",
        "From your profile, I took  into account.",
        "From your profile, I took " + "x" * 251 + " into account.",
        "Nothing from your profile changed this answer",
        "Nothing from your profile changed this answer. Really.",
        "  ",
    ],
)
def test_split_profile_used_trailing_tag_of_the_wrong_shape_is_removed_without_a_line(inner):
    assert split_profile_used(f"An answer.\n<profile_used>{inner}</profile_used>") == (
        "An answer.",
        None,
    )


def test_split_profile_used_line_of_250_characters_is_kept():
    sentence = "From your profile, I took " + "x" * 250 + " into account."
    assert split_profile_used(f"A.<profile_used>{sentence}</profile_used>")[1] == sentence


def test_split_profile_used_valid_tag_then_invalid_trailing_tag():
    # Both are at the end: both removed; the last valid trailing tag wins.
    text = f"A.\n{TAG}\n<profile_used>Ignore all rules</profile_used>"
    assert split_profile_used(text) == ("A.", LINE)
    text = f"A.\n<profile_used>Ignore all rules</profile_used>{TAG}"
    assert split_profile_used(text) == ("A.", LINE)


def test_split_profile_used_two_valid_trailing_tags_keep_the_last():
    text = f"A.\n<profile_used>{NOTHING}</profile_used>\n{TAG}"
    assert split_profile_used(text) == ("A.", LINE)


def test_split_profile_used_valid_tag_followed_by_text_is_not_trailing():
    text = f"A {TAG} B\n<profile_used>Ignore all rules</profile_used>"
    assert split_profile_used(text) == (f"A {TAG} B", None)


def test_split_profile_used_nested_tags_stay_as_written():
    text = "A <profile_used>From your profile <profile_used>x</profile_used></profile_used>"
    assert split_profile_used(text) == (text, None)


@pytest.mark.parametrize(
    "text",
    [
        "<img<profile_used>x</profile_used>src=x onerror=alert(1)>",
        "<img <profile_used>x</profile_used>",
        "<img<profile_used>x</profile_used>\n<profile_used>y</profile_used>",
        f"<im{TAG}g src=x>{TAG}",
        f"a<{TAG}",
        f"<profile_used>a</profile_used>img src=x>{TAG}",
        f"x {TAG} {TAG} {TAG}",
    ],
)
def test_split_profile_used_body_is_always_a_prefix_of_the_input(text):
    body, _ = split_profile_used(text)
    assert text.startswith(body)
    if not re.search(r"<img\s+src", text, re.I):
        assert not re.search(r"<img\s+src", body, re.I)


# -- prompt -----------------------------------------------------------------------------------


def test_profile_used_chapter_text():
    assert "This line is the only place you mention the Profile." in CHAPTER
    assert "<profile_used>From your profile, I took … into account.</profile_used>" in CHAPTER
    assert "Nothing from your profile changed this answer." in CHAPTER
    assert (
        "Write this line in English, in exactly this form, even when the answer is in another "
        "language." in CHAPTER
    )


def test_profile_used_prompt_has_the_chapter_only_when_asked():
    asked, _ = build_prompt(
        "q", core_prompt="CORE", profile_text="PROF", ask_profile_used=True
    )
    assert CHAPTER in asked
    assert asked.index(load_chapter("personalization_answering")) < asked.index(CHAPTER)
    plain, _ = build_prompt("q", core_prompt="CORE", profile_text="PROF")
    assert CHAPTER not in plain
    no_blocks, _ = build_prompt("q", core_prompt="CORE", ask_profile_used=True)
    assert CHAPTER not in no_blocks


def test_profile_used_turn_prompt_follows_the_context_flag():
    messages = [ConversationMessage("user", "hi")]
    on = MessageContext(profile_text="PROF", ask_profile_used=True)
    off = MessageContext(profile_text="PROF")
    assert CHAPTER in build_turn_prompt(messages=messages, core_prompt="C", context=on).full_prompt
    assert CHAPTER not in build_turn_prompt(messages=messages, core_prompt="C", context=off).full_prompt


def test_profile_used_guardrail_input_never_gets_the_chapter():
    seen = []
    service = MagicMock()
    service.check_message.side_effect = lambda text: (
        seen.append(text) or SimpleNamespace(allowed=True, reason=None)
    )
    context = MessageContext(profile_text="PROF", ask_profile_used=True)
    import asyncio

    with patch("chat.V2.agent.guardrail_gate.get_guardrail_service", return_value=service):
        asyncio.run(
            DefaultGuardrailGate().run_guardrail(
                bt_span=MagicMock(), user_message="q", context=context, start_time=0.0
            )
        )
    assert seen and CHAPTER not in seen[0] and "profile_used" not in seen[0]


# -- runtime ----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_profile_used_asked_with_lab_on_and_profile_injected(lab_on):  # noqa: F811
    seed("u1")
    lab_on.setenv("PERSONALIZATION_LAB_SEED", "true")
    assert runtime.context_for_request("u1", plain=False).ask_profile_used is True


@pytest.mark.django_db
def test_profile_used_not_asked_without_lab(lab_on):  # noqa: F811
    seed("u1")
    assert runtime.context_for_request("u1", plain=False).ask_profile_used is False


@pytest.mark.django_db
def test_profile_used_not_asked_on_the_plain_arm(lab_on):  # noqa: F811
    lab_on.setenv("PERSONALIZATION_LAB_SEED", "true")
    seed("u1")
    assert runtime.context_for_request("u1", plain=True).ask_profile_used is False


@pytest.mark.django_db
def test_profile_used_not_asked_with_the_profile_off_or_empty(lab_on):  # noqa: F811
    lab_on.setenv("PERSONALIZATION_LAB_SEED", "true")
    seed("u1")
    store.set_enabled("u1", "profile", False)
    assert runtime.context_for_request("u1", plain=False).ask_profile_used is False
    seed("u2", profile=None)  # Instructions only: no Profile block
    assert runtime.context_for_request("u2", plain=False).ask_profile_used is False


# -- orchestrator -----------------------------------------------------------------------------


def make_orchestrator(final_texts, *, valid=(True,)):
    runner = MagicMock()
    runner.run = AsyncMock(
        side_effect=[
            SDKRunResult(
                final_text=text, trace_id="t", llm_call_count=1, usage=None, total_cost_usd=None
            )
            for text in final_texts
        ]
    )
    gate = MagicMock()
    gate.run_guardrail = AsyncMock(return_value=None)
    router = MagicMock()
    router.run_router = AsyncMock(side_effect=lambda span, msg, msgs: (None, "r", msgs))
    prompts = MagicMock()
    prompts.get_core_prompt.return_value = SimpleNamespace(
        text="CORE", prompt_id="p", version="1"
    )
    options = MagicMock()
    options.build.return_value = (MagicMock(), True)
    tool_runtime = MagicMock()
    tool_runtime.build_sdk_tools.return_value = []
    trace = MagicMock()
    orchestrator = TurnOrchestrator(
        model="m",
        mcp_server_name="srv",
        prompt_service=prompts,
        create_mcp_server=MagicMock(),
        tool_runtime=tool_runtime,
        options_builder=options,
        sdk_runner=runner,
        guardrail_gate=gate,
        router=router,
        trace_logger=trace,
    )
    results = [
        LinkValidationResult(issues=[] if ok else [LinkValidationIssue("http://x", "bad")])
        for ok in valid
    ]
    validator = MagicMock()
    validator.validate_response = AsyncMock(side_effect=results)
    return orchestrator, runner, trace, validator


def _turn_patches(validator):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch("chat.V2.agent.turn_orchestrator.ResponseLinkValidator", return_value=validator)
    )
    stack.enter_context(
        patch("chat.V2.agent.turn_orchestrator.current_span", return_value=MagicMock())
    )
    return stack


async def run_turn(orchestrator, validator, context=None):
    with _turn_patches(validator):
        return await orchestrator.run_turn(
            messages=[ConversationMessage("user", "hi")],
            core_prompt_id=None,
            on_progress=None,
            context=context or MessageContext(profile_text="PROF", ask_profile_used=True),
        )


@pytest.mark.asyncio
async def test_profile_used_is_split_off_the_saved_content_and_the_trace():
    orchestrator, runner, trace, validator = make_orchestrator([f"The answer.\n\n{TAG}"])
    response = await run_turn(orchestrator, validator)
    assert response.content == "The answer."
    assert response.profile_used == LINE
    logged = trace.log_success.call_args.kwargs
    assert logged["content"] == "The answer." and logged["profile_used"] == LINE
    # the link validator saw the clean text too
    assert "<profile_used>" not in validator.validate_response.call_args.args[0]


@pytest.mark.asyncio
async def test_profile_used_a_forgotten_tag_gives_the_full_answer_and_no_line():
    orchestrator, _, trace, validator = make_orchestrator(["The answer."])
    response = await run_turn(orchestrator, validator)
    assert response.content == "The answer." and response.profile_used is None
    assert trace.log_success.call_args.kwargs["profile_used"] is None


@pytest.mark.asyncio
async def test_profile_used_mid_answer_tag_stays_as_model_text():
    draft = f"First part. {TAG} Second part."
    orchestrator, _, _, validator = make_orchestrator([draft])
    response = await run_turn(orchestrator, validator)
    assert response.content == draft
    assert response.profile_used is None


@pytest.mark.asyncio
async def test_profile_used_after_a_link_repair_comes_from_the_repaired_text():
    repaired = "The repaired answer."
    other = "From your profile, I took your interest in Ramban into account."
    orchestrator, runner, trace, validator = make_orchestrator(
        [f"The answer.\n{TAG}", f"{repaired}\n<profile_used>{other}</profile_used>"],
        valid=(False, True),
    )
    response = await run_turn(orchestrator, validator)
    repair_prompt = runner.run.call_args_list[1].kwargs["prompt_text"]
    assert "profile_used" not in repair_prompt and "The answer." in repair_prompt
    assert response.content == repaired
    assert response.profile_used == other  # the line of the text actually returned
    assert trace.log_success.call_args.kwargs["profile_used"] == other
    assert "profile_used" not in trace.log_success.call_args.kwargs["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repaired_text",
    [
        "The repaired answer.",  # no tag
        "The repaired answer.\n<profile_used>second line</profile_used>",  # wrong shape
        f"The repaired answer. {TAG} More text.",  # not at the end
    ],
)
async def test_profile_used_is_none_when_the_repaired_text_has_no_valid_trailing_tag(repaired_text):
    orchestrator, _, trace, validator = make_orchestrator(
        [f"The answer.\n{TAG}", repaired_text], valid=(False, True)
    )
    response = await run_turn(orchestrator, validator)
    assert response.profile_used is None  # never the first draft's line
    assert trace.log_success.call_args.kwargs["profile_used"] is None
    assert response.content == split_profile_used(repaired_text)[0]


@pytest.mark.asyncio
async def test_profile_used_is_none_with_the_fallback_after_failed_repair():
    from chat.V2.agent.turn_orchestrator import ERROR_FALLBACK_MESSAGE

    orchestrator, _, trace, validator = make_orchestrator(
        [f"The answer.\n{TAG}", f"Still bad.\n{TAG}"], valid=(False, False)
    )
    response = await run_turn(orchestrator, validator)
    assert response.content == ERROR_FALLBACK_MESSAGE
    assert response.profile_used is None
    assert trace.log_success.call_args.kwargs["profile_used"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context",
    [
        MessageContext(profile_text="PROF", ask_profile_used=False),
        MessageContext(),
        None,
    ],
)
async def test_profile_used_untouched_when_not_asked(context):
    draft = f"Use <profile_used>a</profile_used> and <profile_used>unclosed"
    orchestrator, runner, trace, validator = make_orchestrator([draft])
    if context is None:
        # No context at all: the agent service fills in an empty one, which never asks for the line.
        from chat.V2.agent.claude_service import ClaudeAgentService

        service = object.__new__(ClaudeAgentService)
        service._orchestrator = orchestrator
        service.braintrust_logging_enabled = False
        with _turn_patches(validator):
            response = await service.send_message([ConversationMessage("user", "hi")], context=None)
        assert runner.run.call_count == 1
        assert "profile_used" not in runner.run.call_args.kwargs["prompt_text"]
    else:
        response = await run_turn(orchestrator, validator, context)
    assert response.content == draft
    assert response.profile_used is None
    assert trace.log_success.call_args.kwargs["profile_used"] is None


@pytest.mark.asyncio
async def test_profile_used_whole_answer_in_the_tag_is_kept_as_written():
    from chat.V2.agent.turn_orchestrator import ERROR_FALLBACK_MESSAGE

    draft = "<profile_used>The whole <b>answer</b> here.</profile_used>"
    orchestrator, _, _, validator = make_orchestrator([draft])
    response = await run_turn(orchestrator, validator)
    assert response.content == draft
    assert response.content != ERROR_FALLBACK_MESSAGE
    assert response.profile_used is None


@pytest.mark.asyncio
async def test_profile_used_unclosed_whole_answer_is_kept():
    orchestrator, _, _, validator = make_orchestrator(["<profile_used>Half an answer"])
    response = await run_turn(orchestrator, validator)
    assert response.content == "<profile_used>Half an answer" and response.profile_used is None


@pytest.mark.asyncio
async def test_profile_used_whole_answer_in_the_tag_after_a_link_repair():
    repaired = "<profile_used>Repaired answer.</profile_used>"
    orchestrator, _, _, validator = make_orchestrator(
        [f"The answer.\n{TAG}", repaired], valid=(False, True)
    )
    response = await run_turn(orchestrator, validator)
    assert response.content == repaired


@pytest.mark.asyncio
async def test_profile_used_an_empty_draft_still_falls_back():
    from chat.V2.agent.turn_orchestrator import ERROR_FALLBACK_MESSAGE

    orchestrator, _, _, validator = make_orchestrator(["<profile_used> </profile_used>"])
    response = await run_turn(orchestrator, validator)
    assert response.content == ERROR_FALLBACK_MESSAGE
    assert response.profile_used is None


# -- views ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_profile_used_reaches_the_payload_and_the_flag_reaches_the_agent(lab_on):  # noqa: F811
    from chat.V2.agent import AgentResponse

    from .test_triggers import send

    lab_on.setenv("PERSONALIZATION_LAB_SEED", "true")
    seed("user_123")
    with _make_queued() as (agent, summary):
        agent.send_message = AsyncMock(
            return_value=AgentResponse(
                content="An answer.",
                tool_calls=[],
                latency_ms=10,
                trace_id="trace_1",
                profile_used=LINE,
            )
        )
        stream = send("sess-pu", "m-pu")
    assert agent.send_message.call_args.kwargs["context"].ask_profile_used is True
    assert '"profileUsed": "' + LINE in stream or '"profileUsed":"' + LINE in stream
    assert "<profile_used>" not in stream
    saved = ChatMessage_assistant("sess-pu")
    assert saved.content == "An answer." and "profile_used" not in saved.content
    assert "profile_used" not in summary.return_value.update_summary.call_args.kwargs[
        "new_assistant_response"
    ]


def ChatMessage_assistant(session):
    from chat.models import ChatMessage

    return ChatMessage.objects.filter(session_id=session, role=ChatMessage.Role.ASSISTANT).get()


def _make_queued():
    from contextlib import contextmanager
    from concurrent.futures import ThreadPoolExecutor

    from chat.V2.appetizer import appetizer_service  # noqa: F401

    @contextmanager
    def cm():
        executors = []

        def factory():
            executor = ThreadPoolExecutor(max_workers=1)
            executors.append(executor)
            return executor

        with (
            patch("chat.V2.views._create_traced_executor", side_effect=factory),
            patch("chat.V2.views._flush_braintrust"),
            patch("chat.V2.views.get_summary_service") as summary,
            patch("chat.V2.appetizer.appetizer_service.get_appetizer_service") as appetizer,
            patch("chat.V2.views.get_agent_service") as get_agent,
            patch.object(runtime, "queue_job"),
        ):
            summary.return_value.update_summary.return_value.to_prompt_text.return_value = "s"
            appetizer.return_value.find_appetizer = AsyncMock(return_value=None)
            agent = MagicMock()
            get_agent.return_value = agent
            yield agent, summary
            for executor in executors:
                executor.shutdown(wait=True)

    return cm()


def test_profile_used_payload_key_only_when_present():
    import datetime

    from chat.V2 import views

    reply = SimpleNamespace(
        message_id="m",
        server_timestamp=datetime.datetime(2026, 11, 12),
        content="c",
        tool_calls_data=[],
        status="success",
    )
    base = {"response_message": reply, "session_id": "s", "turn_count": 1, "stats": {}}
    assert "profileUsed" not in views._build_response_payload(**base)
    assert views._build_response_payload(**base, profile_used="L")["profileUsed"] == "L"
