"""The Jev gate: closed questions over screened prompts. Fake Jev client only; made-up text."""

import pytest

from chat.V2.personalization import jev_gate
from chat.V2.personalization import jev_questions as q
from chat.V2.personalization.core_slots import CORE_KEYS
from chat.V2.personalization.jev import FakeJevClient, JevAnswer, JevError
from chat.V2.personalization.jev_gate import ask_gate, same_heading

PROMPTS = ["explain rashi on the first verse", "show me a short summary", "thanks"]


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-not-real")


def answering(default, **by_key):
    def responder(state, questions):
        return {k: JevAnswer("noul", noul=by_key.get(k, default)) for k in questions}

    return FakeJevClient(responder)


def test_off_returns_everything_without_calling_jev():
    jev = answering(0.0)
    result = ask_gate(PROMPTS, "off", jev)
    assert result.relevant and result.slots == set(CORE_KEYS)
    assert result.sensitive == set() and result.source == "off"
    assert jev.calls == []


def test_on_all_clear_no_is_not_relevant():
    result = ask_gate(PROMPTS, "on", answering(0.1))
    assert result.relevant is False and result.source == "jev"
    assert result.slots == set() and result.sensitive == set()


def test_on_picks_slots_and_sensitive_prompts():
    jev = answering(
        0.1, relevant=0.9, slot_answer_style=0.8, slot_commentators=0.4, sensitive_1=0.7
    )
    result = ask_gate(PROMPTS, "on", jev)
    assert result.relevant and result.slots == {"answer_style", "commentators"}
    assert result.sensitive == {1}


def test_uncertain_answer_counts_as_yes():
    result = ask_gate(PROMPTS, "on", answering(0.5))
    assert result.relevant and result.slots == set(CORE_KEYS)
    assert result.sensitive == set(range(len(PROMPTS)))


def test_band_edges():
    assert ask_gate(PROMPTS, "on", answering(0.35)).relevant
    assert ask_gate(PROMPTS, "on", answering(0.65)).relevant
    assert not ask_gate(PROMPTS, "on", answering(0.349)).relevant


@pytest.mark.parametrize("error", [JevError("Jev HTTP 429"), JevError("network"), TimeoutError()])
def test_errors_fall_back_to_the_model_path(error):
    def boom(state, questions):
        raise error

    result = ask_gate(PROMPTS, "on", FakeJevClient(boom))
    assert result.source == "fallback" and result.relevant and result.slots == set(CORE_KEYS)
    assert result.sensitive == set()


def test_no_api_key_means_no_call(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    jev = answering(0.0)
    result = ask_gate(PROMPTS, "on", jev)
    assert result.source == "fallback" and result.relevant and jev.calls == []


def test_shadow_logs_and_returns_the_off_result(monkeypatch):
    logged = []
    monkeypatch.setattr(
        jev_gate.logger, "info", lambda msg, *args: logged.append(msg % args if args else msg)
    )
    jev = answering(0.1)
    result = ask_gate(PROMPTS, "shadow", jev)
    assert result.relevant and result.slots == set(CORE_KEYS) and result.source == "off"
    assert len(jev.calls) == 1
    assert any("relevant" in line for line in logged) and result.scores["relevant"] == 0.1


@pytest.mark.parametrize("count", [1, 5, 30])
def test_one_request_for_any_number_of_prompts(count):
    jev = answering(0.9)
    ask_gate([f"prompt {i}" for i in range(count)], "on", jev)
    assert len(jev.calls) == 1
    assert len(jev.calls[0][0]["learner_prompts"]) == count


def test_the_model_is_pinned():
    assert q.MODEL == "jev-1.13.0"


def test_same_heading_verdicts(settings):
    settings.PERSONALIZATION_JEV_GATE = "on"
    assert same_heading("Teaching", "Teaching work", answering(0.9)) is True
    assert same_heading("Teaching", "Rhythm", answering(0.1)) is False
    assert same_heading("Teaching", "Teaching work", answering(0.5)) is None


def test_same_heading_without_jev_or_on_error_is_uncertain(settings, monkeypatch):
    settings.PERSONALIZATION_JEV_GATE = "on"

    def boom(state, questions):
        raise JevError("Jev HTTP 429")

    assert same_heading("a", "b", FakeJevClient(boom)) is None
    settings.PERSONALIZATION_JEV_GATE = "off"
    assert same_heading("a", "b", answering(0.9)) is None
    settings.PERSONALIZATION_JEV_GATE = "on"
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert same_heading("a", "b", answering(0.9)) is None
