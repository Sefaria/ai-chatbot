import json

from chat.V2.personalization.jev import FakeJevClient, JevAnswer
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.typed import (
    HaikuTypedClient,
    JevTypedClient,
    TypedAnswer,
    TypedQuestion,
)

from .conftest import NOW, make_strategy, make_turn

QUESTIONS = [
    TypedQuestion("hebrew_fluency", "choice", "How well does the learner read Hebrew?",
                  {"none": "cannot", "fluent": "reads freely", "unclear": "no evidence"}),
    TypedQuestion("category:Talmud", "noul", "Do the prompts ask about the Talmud?"),
]  # fmt: skip


def haiku(reply):
    llm = FakeLLMClient(lambda system, user: reply if isinstance(reply, str) else json.dumps(reply))
    return HaikuTypedClient(llm, model="claude-haiku-5-5"), llm


def test_haiku_typed_client_parses_structured_reply():
    client, llm = haiku(
        {
            "answers": {
                "hebrew_fluency": {"choice": "none", "confidence": 0.8},
                "category:Talmud": {"noul": 0.9},
            }
        }
    )
    answers = client.answer('{"learner_prompts": []}', QUESTIONS)
    assert answers == [
        TypedAnswer("hebrew_fluency", choice="none", confidence=0.8),
        TypedAnswer("category:Talmud", noul=0.9),
    ]
    assert len(llm.calls) == 1 and llm.calls[0]["model"] == "claude-haiku-5-5"
    assert "How well does the learner read Hebrew?" in llm.calls[0]["system"] + llm.calls[0]["user"]


def test_haiku_typed_malformed_reply_is_no_answer():
    for reply in ("not json at all", {"answers": "nope"}, {"x": 1}, "[]"):
        client, _ = haiku(reply)
        answers = client.answer("{}", QUESTIONS)
        assert [a.key for a in answers] == ["hebrew_fluency", "category:Talmud"]
        assert all(a.choice is None and a.noul is None and a.confidence is None for a in answers)


def test_haiku_typed_bad_entries_are_no_answer_individually():
    client, _ = haiku(
        {
            "answers": {
                "hebrew_fluency": {"choice": "martian", "confidence": 0.9},  # not an option
                "category:Talmud": {"noul": 7},  # out of range
            }
        }
    )
    assert all(a.choice is None and a.noul is None for a in client.answer("{}", QUESTIONS))


def test_jev_typed_client_wraps_the_jev_client():
    jev = FakeJevClient(
        lambda state, questions: {
            "hebrew_fluency": JevAnswer("choice", choice="fluent", confidence=0.7),
            "category:Talmud": JevAnswer("noul", noul=0.2),
        }
    )
    answers = JevTypedClient(jev).answer('{"learner_prompts": [1]}', QUESTIONS)
    assert answers[0] == TypedAnswer("hebrew_fluency", choice="fluent", confidence=0.7)
    assert answers[1] == TypedAnswer("category:Talmud", noul=0.2)
    assert jev.calls[0][0] == {"learner_prompts": [1]}


def test_hybrid_haiku_strategy_registered(store, registry):
    reply = {
        "answers": {
            "hebrew_fluency": {"choice": "none", "confidence": 0.9},
            "register": {"choice": "academic", "confidence": 0.9},
        }
    }
    strategy, llm, _ = make_strategy(
        "hybrid_haiku", store, registry, lambda system, user: json.dumps(reply)
    )
    assert strategy.name == "hybrid_haiku"
    strategy.on_turn(make_turn("Cannot read Hebrew; compare the Rambam with the scholarship."), NOW)
    profile = store.get_profile("u1")
    assert profile["hebrew_fluency"].value == "none"
    assert profile["register"].value == "academic"
    assert len(llm.calls) == 1


def test_hybrid_haiku_is_a_valid_mode():
    from chat.V2.personalization.gate import MODES

    assert "hybrid_haiku" in MODES
