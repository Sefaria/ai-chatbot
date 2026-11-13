import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from chat.V2.personalization.jev import (
    FakeJevClient,
    HttpJevClient,
    JevAnswer,
    JevError,
    JevQuestion,
)
from chat.V2.personalization.llm import estimate_cost
from chat.V2.personalization.strategies import jev_questions as q

SECRET = "fake-test-value-123"


def urlopen_returning(payload):
    response = MagicMock()
    response.__enter__.return_value = io.BytesIO(json.dumps(payload).encode())
    return MagicMock(return_value=response)


QUESTIONS = {
    "fluency": JevQuestion("choice", "How fluent?", {"none": "no Hebrew", "unclear": None}),
    "style": JevQuestion("noul", "Is it a preference?"),
}
REPLY = {
    "answers": {
        "fluency": {
            "type": "choice",
            "choice": "none",
            "confidence": 0.9,
            "probabilities": {"none": 0.9},
        },
        "style": {"type": "noul", "noul": 0.93},
    },
    "usage": {"input_tokens": 321},
    "model": "jev-1.13.0",
}


def test_http_request_shape_and_parsing(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", SECRET)
    opener = urlopen_returning(REPLY)
    with patch("chat.V2.personalization.jev.urllib.request.urlopen", opener):
        response = HttpJevClient().ask({"learner_prompts": []}, QUESTIONS)
    request = opener.call_args.args[0]
    assert request.full_url == "https://api.typesafe.ai/v1/systemone" and request.method == "POST"
    assert request.get_header("Authorization") == f"Bearer {SECRET}"
    assert opener.call_args.kwargs["timeout"] == 30
    body = json.loads(request.data)
    assert body["model"] == "jev-1.13.0" and body["state"] == {"learner_prompts": []}
    assert body["questions"]["style"] == {"type": "noul", "instructions": "Is it a preference?"}
    assert body["questions"]["fluency"]["criteria"] == {"none": "no Hebrew", "unclear": None}
    assert response.input_tokens == 321 and response.model == "jev-1.13.0"
    assert (
        response.answers["fluency"].choice == "none"
        and response.answers["fluency"].confidence == 0.9
    )
    assert response.answers["style"].noul == 0.93


def test_http_error_reports_status_only(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", SECRET)
    error = urllib.error.HTTPError(
        "https://x", 422, f"bad {SECRET}", {}, io.BytesIO(SECRET.encode())
    )
    with patch("chat.V2.personalization.jev.urllib.request.urlopen", side_effect=error):
        with pytest.raises(JevError) as raised:
            HttpJevClient().ask({}, QUESTIONS)
    assert "422" in str(raised.value) and SECRET not in str(raised.value)
    assert SECRET not in repr(raised.value.__cause__) and raised.value.__suppress_context__


def test_network_error_is_a_jev_error_without_the_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", SECRET)
    with patch(
        "chat.V2.personalization.jev.urllib.request.urlopen",
        side_effect=urllib.error.URLError(SECRET),
    ):
        with pytest.raises(JevError) as raised:
            HttpJevClient().ask({}, QUESTIONS)
    assert SECRET not in str(raised.value)


def test_no_key_raises_without_a_request(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    opener = MagicMock()
    with patch("chat.V2.personalization.jev.urllib.request.urlopen", opener):
        with pytest.raises(JevError, match="no Jev API key"):
            HttpJevClient().ask({}, QUESTIONS)
    opener.assert_not_called()


def test_keychain_only_when_allowed(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    done = MagicMock(stdout=f"{SECRET}\n")
    with patch("chat.V2.personalization.jev.subprocess.run", return_value=done) as run:
        with pytest.raises(JevError):
            HttpJevClient(allow_keychain=False).ask({}, QUESTIONS)
        run.assert_not_called()
        opener = urlopen_returning(REPLY)
        with patch("chat.V2.personalization.jev.urllib.request.urlopen", opener):
            HttpJevClient(allow_keychain=True).ask({}, QUESTIONS)
        assert run.call_args.args[0][:3] == ["security", "find-generic-password", "-s"]
        assert opener.call_args.args[0].get_header("Authorization") == f"Bearer {SECRET}"


def test_fake_client_counts_tokens_and_records_calls():
    client = FakeJevClient(lambda state, questions: {"style": JevAnswer("noul", noul=0.5)})
    response = client.ask({"message": "x" * 400}, QUESTIONS)
    assert response.input_tokens > 100 and response.answers["style"].noul == 0.5
    assert len(client.calls) == 1


def test_jev_price_is_in_the_table():
    assert estimate_cost("jev-1.13.0", 1_000_000, 5_000_000) == pytest.approx(0.042)


# -- question definitions ------------------------------------------------------------------


def test_choice_options_match_the_registry(registry):
    questions = q.choice_questions(registry)
    for key in q.CHOICE_SLOTS:
        options = list(questions[key].criteria)
        assert options == [*registry.get(key).allowed, q.UNCLEAR]
        assert all(questions[key].criteria[o] for o in options)  # a one-line description each


def test_category_questions_cover_every_registry_category(registry):
    questions = q.category_questions(registry)
    allowed = registry.get("text_categories").allowed
    assert list(questions) == [f"category:{c}" for c in allowed]
    assert set(q.CATEGORY_DESCRIPTIONS) == set(allowed)
    assert all(x.type == "noul" and "learner_prompts" in x.instructions for x in questions.values())


def test_instruction_questions_and_pinned_values():
    questions = q.instruction_questions()
    assert set(questions) == {q.STYLE_KEY, q.EXCLUDE_KEY}
    assert all("`message`" in x.instructions for x in questions.values())
    assert q.JEV_MODEL == "jev-1.13.0"
    assert (q.SET_MIN_CONFIDENCE, q.CHANGE_MIN_CONFIDENCE) == (0.5, 0.75)
    assert (q.CATEGORY_MIN_NOUL, q.INSTRUCTION_MIN_NOUL) == (0.65, 0.65)
    assert (q.WINDOW_PROMPTS, q.PROMPT_CHARS) == (12, 500)
