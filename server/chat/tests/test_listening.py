"""Listening isolation, idempotency, lease cancellation and tool-free model contracts."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from chat.auth.actor import Actor
from chat.listening import model_service
from chat.models import ChatMessage, ListeningSession

pytestmark = pytest.mark.django_db
BASE = "/api/v2/listening/sessions"


@pytest.fixture
def client(settings):
    settings.LISTENING_ENABLED = True
    with patch("chat.listening.views.authenticate_request", return_value=Actor(user_id="owner")):
        yield APIClient()


def start(client):
    response = client.post(BASE, {"userId": "token"}, format="json")
    assert response.status_code == 201
    return response.data["session_id"]


def batch(*texts):
    return {
        "request_id": str(uuid.uuid4()),
        "segments": [
            {"seq": i + 1, "text": text, "page_url": "https://www.sefaria.org/Berakhot.2a"}
            for i, text in enumerate(texts)
        ],
    }


def analyze(client, sid, body):
    return client.post(f"{BASE}/{sid}/analyze", body, format="json")


def unthrottle(sid):
    ListeningSession.objects.filter(id=sid).update(next_analysis_at=None)


@patch("chat.listening.model_service.reword", return_value="Why is this question inside a Mishnah?")
@patch("chat.listening.model_service.detect", return_value={"start_seq": 1, "end_seq": 1})
def test_suggest_and_replay_without_creating_chat(detect, reword, client):
    sid = start(client)
    body = batch("I have never seen this pattern before.")
    response = analyze(client, sid, body)
    assert response.status_code == 200
    assert response.data["analyzed_through_seq"] == 1
    card = response.data["suggestions"][0]
    assert card["page_url"] == body["segments"][0]["page_url"]
    replay = analyze(client, sid, body)
    assert replay.data == response.data
    detect.assert_called_once()
    reword.assert_called_once()
    assert ChatMessage.objects.count() == 0
    body["segments"][0]["text"] = "Changed"
    assert analyze(client, sid, body).status_code == 409


@patch("chat.listening.model_service.reword")
@patch("chat.listening.model_service.detect", return_value=None)
def test_no_question_never_calls_sonnet(detect, reword, client):
    sid = start(client)
    assert analyze(client, sid, batch("Reading an ordinary statement.")).data["suggestions"] == []
    reword.assert_not_called()


@patch("chat.listening.model_service.detect", return_value={"start_seq": 1, "end_seq": 1})
def test_sonnet_failure_retries_checkpoint_without_rerunning_haiku(detect, client):
    sid = start(client)
    body = batch("Why is this here?")
    with patch("chat.listening.model_service.reword", side_effect=RuntimeError("provider")):
        assert analyze(client, sid, body).status_code == 503
    session = ListeningSession.objects.get(id=sid)
    assert session.state["received"] == 1
    assert session.state["analyzed"] == 0
    assert session.lease is None
    unthrottle(sid)
    with patch("chat.listening.model_service.reword", return_value="Why is this here?"):
        assert analyze(client, sid, body).status_code == 200
    detect.assert_called_once()


@patch("chat.listening.model_service.detect")
def test_stop_during_detection_discards_result(detect, client):
    sid = start(client)

    def stop_during_call(payload):
        assert client.post(f"{BASE}/{sid}/stop", {}, format="json").status_code == 200
        return {"start_seq": 1, "end_seq": 1}

    detect.side_effect = stop_during_call
    with patch("chat.listening.model_service.reword") as reword:
        assert analyze(client, sid, batch("Why?")).status_code == 410
        reword.assert_not_called()
    session = ListeningSession.objects.get(id=sid)
    assert session.closed and session.state == {}


def test_owner_expiry_flag_and_anonymous_isolation(client, settings):
    sid = start(client)
    with patch("chat.listening.views.authenticate_request", return_value=Actor(user_id="other")):
        assert analyze(client, sid, batch("Why?")).status_code == 404
        assert client.post(f"{BASE}/{sid}/stop", {}, format="json").status_code == 404
    with patch(
        "chat.listening.views.authenticate_request",
        return_value=Actor(user_id="anon", is_anonymous=True),
    ):
        assert client.post(BASE, {}, format="json").status_code == 401
    ListeningSession.objects.filter(id=sid).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert analyze(client, sid, batch("Why?")).status_code == 410
    settings.LISTENING_ENABLED = False
    assert client.get(BASE).data["enabled"] is False
    assert client.post(BASE, {}, format="json").status_code == 404


@patch("chat.listening.model_service.detect", return_value=None)
def test_sequence_conflicts_busy_and_validation(detect, client):
    sid = start(client)
    body = batch("Hello")
    body["segments"][0]["seq"] = 2
    response = analyze(client, sid, body)
    assert response.status_code == 409 and response.data["expected_seq"] == 1
    unthrottle(sid)
    ListeningSession.objects.filter(id=sid).update(
        lease=uuid.uuid4(), lease_until=timezone.now() + timedelta(seconds=30)
    )
    assert analyze(client, sid, batch("Hi")).status_code == 409
    detect.assert_not_called()
    assert analyze(client, sid, {"request_id": "bad", "segments": []}).status_code == 400


@patch("chat.listening.model_service.reword", return_value="Why is this different?")
@patch("chat.listening.model_service.detect", return_value={"start_seq": 1, "end_seq": 1})
def test_dismissal_and_exact_duplicate_suppression(detect, reword, client):
    sid = start(client)
    first = analyze(client, sid, batch("Why is this different?"))
    card = first.data["suggestions"][0]
    response = client.patch(
        f"{BASE}/{sid}/suggestions/{card['id']}", {"status": "dismissed"}, format="json"
    )
    assert response.status_code == 200
    unthrottle(sid)
    detect.return_value = {"start_seq": 2, "end_seq": 2}
    body = batch("Why is this different?")
    body["segments"][0]["seq"] = 2
    second = analyze(client, sid, body)
    assert len(second.data["suggestions"]) == 1
    assert second.data["suggestions"][0]["status"] == "dismissed"
    assert "Why is this different?" in detect.call_args.args[0]["recent_suggestions"]


@patch("chat.listening.model_service.detect", return_value=None)
def test_large_backlog_drains_without_new_speech(detect, client):
    sid = start(client)
    body = batch(*["x" * 2000] * 5)
    response = analyze(client, sid, body)
    assert response.data["received_through_seq"] == 5
    assert response.data["analyzed_through_seq"] == 3
    unthrottle(sid)
    response = analyze(client, sid, {"request_id": str(uuid.uuid4()), "segments": []})
    assert response.data["analyzed_through_seq"] == 5


def test_cleanup_and_start_budget(client):
    sid = start(client)
    ListeningSession.objects.filter(id=sid).update(expires_at=timezone.now() - timedelta(seconds=1))
    call_command("cleanup_listening")
    assert ListeningSession.objects.get(id=sid).state == {}
    for _ in range(5):
        start(client)
    assert client.post(BASE, {}, format="json").status_code == 429


def response(data, stop_reason="end_turn"):
    import json

    return SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(data))]
    )


@patch("chat.listening.model_service.get_anthropic_client")
def test_model_contract_rejects_ungrounded_or_truncated_output(client_factory):
    client = client_factory.return_value.with_options.return_value
    payload = {
        "context_segments": [{"seq": 1, "text": "Earlier"}],
        "new_segments": [{"seq": 2, "text": "Why?"}],
        "recent_suggestions": [],
    }
    for data in [
        {"has_question": "yes"},
        {"has_question": True, "start_seq": 1, "end_seq": 1},
        {"has_question": True, "start_seq": 2, "end_seq": 99},
    ]:
        client.messages.create.return_value = response(data)
        with pytest.raises(ValueError):
            model_service.detect(payload)
    client.messages.create.return_value = response({"has_question": False}, "max_tokens")
    with pytest.raises(ValueError):
        model_service.detect(payload)
    kwargs = client.messages.create.call_args.kwargs
    assert "tools" not in kwargs
    # anthropic>=2 raises TypeError on sampling kwargs before any request is sent.
    assert not {"temperature", "top_p", "top_k"} & kwargs.keys()


@patch("chat.listening.model_service.get_anthropic_client")
def test_reformulation_skip_and_length_validation(factory):
    client = factory.return_value.with_options.return_value
    client.messages.create.return_value = response({"action": "skip", "question": ""})
    assert model_service.reword({}, {}) is None
    client.messages.create.return_value = response({"action": "suggest", "question": "x" * 601})
    with pytest.raises(ValueError):
        model_service.reword({}, {})


@patch("chat.listening.model_service.detect", return_value={"start_seq": 1, "end_seq": 1})
def test_stop_during_sonnet_prevents_publishing(detect, client):
    sid = start(client)

    def reword_then_stop(payload, evidence):
        client.post(f"{BASE}/{sid}/stop", {}, format="json")
        return "Why is this here?"

    with patch("chat.listening.model_service.reword", side_effect=reword_then_stop):
        assert analyze(client, sid, batch("Why is this here?")).status_code == 410
    assert ListeningSession.objects.get(id=sid).state == {}


@patch("chat.listening.model_service.detect")
def test_stale_worker_cannot_overwrite_replacement_lease(detect, client):
    sid = start(client)
    replacement = uuid.uuid4()

    def lose_lease(payload):
        ListeningSession.objects.filter(id=sid).update(lease=replacement)
        return None

    detect.side_effect = lose_lease
    assert analyze(client, sid, batch("Why?")).status_code == 410
    session = ListeningSession.objects.get(id=sid)
    assert session.lease == replacement
    assert session.state["analyzed"] == 0


@patch("chat.listening.model_service.detect", return_value=None)
def test_cadence_and_attempt_budget_prevent_model_calls(detect, client):
    sid = start(client)
    analyze(client, sid, batch("Ordinary speech"))
    body = batch("More speech")
    body["segments"][0]["seq"] = 2
    assert analyze(client, sid, body).status_code == 429
    session = ListeningSession.objects.get(id=sid)
    session.state["attempts"] = 360
    session.next_analysis_at = None
    session.save()
    assert analyze(client, sid, body).status_code == 429
    detect.assert_called_once()
