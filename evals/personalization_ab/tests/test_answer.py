from datetime import UTC, datetime, timedelta

from personalization_ab.answer import Answer, answer_turn
from personalization_ab.select import Turn

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class FakeResponse:
    def __init__(self, content, trace_id="tr-1"):
        self.content = content
        self.trace_id = trace_id


def history():
    return [
        Turn("u", "s", i, T0 + timedelta(minutes=i), f"question {i}", f"answer {i}")
        for i in range(3)
    ]


def test_same_history_reaches_a_and_b_and_only_the_profile_differs():
    seen = []

    def runner(messages, context):
        seen.append((list(messages), context))
        return FakeResponse("ok")

    h = history()
    a = answer_turn("u", h, "next?", "PROFILE A", runner=runner)
    answer_turn("u", h, "next?", "PROFILE B", runner=runner)
    assert isinstance(a, Answer) and a.text == "ok" and a.trace_id == "tr-1"
    (msgs_a, ctx_a), (msgs_b, ctx_b) = seen
    assert msgs_a == msgs_b
    assert [(m.role, m.content) for m in msgs_a] == [
        ("user", "question 0"), ("assistant", "answer 0"),
        ("user", "question 1"), ("assistant", "answer 1"),
        ("user", "question 2"), ("assistant", "answer 2"),
        ("user", "next?"),
    ]  # fmt: skip
    assert (ctx_a.profile_text, ctx_b.profile_text) == ("PROFILE A", "PROFILE B")
    assert ctx_a.instructions_text is None


def test_no_profile_means_the_original_run_has_none():
    seen = []
    answer_turn(
        "u", [], "hi", None, runner=lambda m, c: seen.append(c) or FakeResponse("x")
    )
    assert seen[0].profile_text is None
