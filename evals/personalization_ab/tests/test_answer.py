from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from personalization_ab.answer import Answer, answer_turn, session_summary
from personalization_ab.select import Turn

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeResponse:
    def __init__(self, content, trace_id="tr-1"):
        self.content = content
        self.trace_id = trace_id


def turn(i, session="s1"):
    return Turn(
        "u", session, i, T0 + timedelta(minutes=i), f"question {i}", f"answer {i}"
    )


def test_a_and_b_get_one_message_and_the_same_summary_and_only_the_profile_differs():
    seen = []

    def runner(messages, context):
        seen.append((list(messages), context))
        return FakeResponse("ok")

    a = answer_turn("u", "next?", "PROFILE A", "SUMMARY", turn_number=4, runner=runner)
    answer_turn("u", "next?", "PROFILE B", "SUMMARY", turn_number=4, runner=runner)
    assert isinstance(a, Answer) and a.text == "ok" and a.trace_id == "tr-1"
    (msgs_a, ctx_a), (msgs_b, ctx_b) = seen
    assert msgs_a == msgs_b
    assert [(m.role, m.content) for m in msgs_a] == [("user", "next?")]
    assert ctx_a.summary_text == ctx_b.summary_text == "SUMMARY"
    assert (ctx_a.profile_text, ctx_b.profile_text) == ("PROFILE A", "PROFILE B")
    assert ctx_a.instructions_text is None and ctx_a.turn_number == 4


def test_no_profile_and_no_summary_mean_none():
    seen = []
    answer_turn(
        "u", "hi", None, "", runner=lambda m, c: seen.append(c) or FakeResponse("x")
    )
    assert seen[0].profile_text is None and seen[0].summary_text is None


def test_summary_is_built_once_from_the_same_session_before_the_prompt():
    from personalization_ab.run_ab import answer_pair

    turns = [turn(0, "s1"), turn(1, "s2"), turn(2, "s1"), turn(3, "s2"), turn(4, "s1")]
    builds, calls = [], []

    def builder(earlier, key):
        builds.append([t.prompt for t in earlier])
        return f"SUM{len(builds)}"

    def answerer(user, prompt, profile, summary, *, turn_number):
        calls.append((prompt, profile, summary, turn_number))
        return Answer("x", None)

    answer_pair("u", turns, 4, "PA", "PB", answerer, builder)
    assert builds == [
        ["question 0", "question 2"]
    ]  # one build, s1 only, before prompt 4
    assert calls == [
        ("question 4", "PA", "SUM1", 3),
        ("question 4", "PB", "SUM1", 3),
    ]


@pytest.mark.django_db
def test_session_summary_replays_turns_through_the_summary_service_and_cleans_up():
    from chat.models import ChatSession

    inputs = []

    class FakeService:
        def update_summary(self, session, new_user_message, new_assistant_response):
            from chat.models import ConversationSummary

            inputs.append((new_user_message, new_assistant_response))
            ConversationSummary.objects.update_or_create(
                session=session, defaults={"text": f"after {len(inputs)} turns"}
            )

    with patch(
        "chat.V2.summarization.summary_service.get_summary_service",
        return_value=FakeService(),
    ):
        text = session_summary([turn(0), turn(1)], "k1")
        assert session_summary([], "k2") == ""
    assert inputs == [("question 0", "answer 0"), ("question 1", "answer 1")]
    assert "after 2 turns" in text
    assert not ChatSession.objects.filter(session_id__startswith="ab-sum-").exists()
