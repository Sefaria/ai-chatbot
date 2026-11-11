"""The chat request wired to the profile: one gate, no cost when off, never a failed turn."""

import threading
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.db import ProgrammingError, connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from chat.models import ChatMessage, ChatSession, ProfileSlot
from chat.tests.test_streaming_integration import (  # noqa: F401
    create_test_token,
    mock_auxiliary_services,
)
from chat.user_token_service import decrypt_chatbot_user_identity
from chat.V2.agent import AgentResponse
from chat.V2.agent.contracts import ConversationMessage, MessageContext
from chat.V2.agent.prompt_pipeline import build_turn_prompt
from chat.V2.agent.trace_logger import BraintrustTraceLogger
from chat.V2.personalization import runtime
from chat.V2.personalization.service import ProfileContext
from chat.V2.personalization.store import DjangoProfileStore
from chat.V2.personalization.strategies.base import MemoryStrategy, TurnResult, _current_run
from chat.V2.personalization.types import Turn

SECRET = "wiring-test-secret"
TOKEN = create_test_token("wired-user", SECRET)
USER_ID = decrypt_chatbot_user_identity(TOKEN, SECRET).user_id
OVERRIDE = {"personalizationMode": "realtime"}


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "preview")


@pytest.fixture
def honoured():
    with override_settings(
        CHATBOT_USER_TOKEN_SECRET=SECRET,
        PERSONALIZATION_OVERRIDE_ENABLED=True,
        PERSONALIZATION_OVERRIDE_USER_IDS=USER_ID,
    ):
        yield


@pytest.fixture
def plain():
    with override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET):
        yield


class FakeService:
    """Stands in for PersonalizationService: no database, records what the request did."""

    def __init__(self, text="- Hebrew reading level: basic", fail_read=None, fail_after=None):
        self.text = text
        self.fail_read = fail_read
        self.fail_after = fail_after
        self.reads: list[str] = []
        self.turns: list[Turn] = []
        self.threads: list[str] = []
        self.spans: list[tuple[float, float]] = []

    def profile_snapshot(self, user_id, now):
        self.reads.append(user_id)
        if self.fail_read:
            raise self.fail_read
        return ProfileContext(self.text, ["hebrew_fluency"], 3, "v1")

    def after_turn(self, turn, now):
        started = time.monotonic()
        self.threads.append(threading.current_thread().name)
        time.sleep(0.05)
        self.turns.append(turn)
        self.spans.append((started, time.monotonic()))
        if self.fail_after:
            raise self.fail_after
        return TurnResult()


class FakeAgent:
    def __init__(self, response=None):
        self.contexts: list[MessageContext] = []
        self.prompts: list[str] = []
        self.response = response

    def service(self):
        async def send(*, messages, core_prompt_id, on_progress, context, should_cancel):
            self.contexts.append(context)
            self.prompts.append(
                build_turn_prompt(
                    messages=messages, core_prompt="CORE", context=context
                ).full_prompt
            )
            return self.response or AgentResponse(
                content="An answer.", tool_calls=[], latency_ms=1, trace_id="t", span_export="exp"
            )

        return MagicMock(send_message=send)


def post(message_id="m1", text="hello", context=None, session="s1", token=TOKEN):
    body = {
        "userId": token,
        "sessionId": session,
        "messageId": message_id,
        "timestamp": timezone.now().isoformat(),
        "text": text,
    }
    if context is not None:
        body["context"] = context
    response = APIClient().post("/api/v2/chat/stream", body, format="json")
    assert response.status_code == 200
    return b"".join(response.streaming_content)


@pytest.fixture
def agent():
    fake = FakeAgent()
    with patch("chat.V2.views.get_agent_service", return_value=fake.service()):
        yield fake


@pytest.fixture
def service():
    fake = FakeService()
    with patch.object(runtime, "get_service", return_value=fake):
        yield fake


@pytest.mark.django_db
def test_off_mode_reads_no_profile_tables(plain, agent):
    with (
        patch.object(runtime, "get_service") as get_service,
        patch.object(runtime, "profile_for_request") as profile_for_request,
        patch.object(runtime, "submit_after_turn") as submit,
        CaptureQueriesContext(connection) as queries,
    ):
        body = post(context={"labs": True, "isStaff": True})
    assert b"event: message" in body
    assert not [q for q in queries if "chat_profile" in q["sql"]]
    get_service.assert_not_called()
    profile_for_request.assert_not_called()
    submit.assert_not_called()
    assert agent.contexts[0].personalization_meta == {
        "personalized": False,
        "personalization_source": "off",
    }


@pytest.mark.django_db
@pytest.mark.parametrize("override", [None, OVERRIDE])
def test_off_prompt_through_the_view_equals_no_profile_prompt(plain, agent, override):
    # Without the flag the override is refused; the prompt must be exactly the no-profile prompt.
    post(context=override)
    seen = agent.contexts[0]
    assert seen.profile_text is None and seen.profile_slots_used == []
    bare = MessageContext(
        summary_text=seen.summary_text,
        page_url=seen.page_url,
        session_id=seen.session_id,
        origin=seen.origin,
        user_id=seen.user_id,
    )
    expected = build_turn_prompt(
        messages=[ConversationMessage("user", "hello")], core_prompt="CORE", context=bare
    ).full_prompt
    assert agent.prompts[0] == expected
    assert "<learner_profile>" not in agent.prompts[0]


@pytest.mark.django_db
def test_two_quick_turns_of_one_user_both_apply(honoured, agent, service):
    post("m1", context=OVERRIDE)
    post("m2", context=OVERRIDE)
    assert runtime.wait_for_idle(5)
    assert [t.message_id for t in service.turns] == ["m1", "m2"]
    (s1, e1), (s2, e2) = service.spans
    assert e1 <= s2  # one learner's jobs never overlap
    assert {t.user_id for t in service.turns} == {USER_ID}


@pytest.mark.django_db
def test_profile_reaches_message_context(honoured, agent, service):
    post(context=OVERRIDE)
    seen = agent.contexts[0]
    assert seen.profile_text == "- Hebrew reading level: basic"
    assert seen.profile_slots_used == ["hebrew_fluency"]
    assert "<learner_profile>" in agent.prompts[0]
    assert service.reads == [USER_ID]


@pytest.mark.django_db
def test_missing_tables_do_not_fail_the_turn(honoured, agent):
    broken = FakeService(fail_read=ProgrammingError("relation chat_profileslot does not exist"))
    with patch.object(runtime, "get_service", return_value=broken):
        body = post(context=OVERRIDE)
    assert b"event: message" in body
    assert agent.contexts[0].profile_text is None
    assert agent.contexts[0].personalization_meta["personalized"] is False
    assert ChatMessage.objects.filter(role="assistant").count() == 1


@pytest.mark.django_db
def test_after_turn_runs_off_thread_and_errors_are_swallowed(honoured, agent):
    broken = FakeService(fail_after=ProgrammingError("no table"))
    with (
        patch.object(runtime, "get_service", return_value=broken),
        patch.object(runtime, "capture_exception") as capture,
    ):
        body = post(context=OVERRIDE)
        assert runtime.wait_for_idle(5)
    assert b"event: message" in body
    assert broken.threads and all(n.startswith("personalization") for n in broken.threads)
    assert threading.current_thread().name not in broken.threads
    capture.assert_called()


def test_after_turn_gets_a_fresh_run_state_per_job():
    seen = []

    class Service:
        def after_turn(self, turn, now):
            before = _current_run.get()
            with MemoryStrategy.collecting() as run:
                run.writes_applied += 1
                time.sleep(0.02)
                seen.append((before, run.writes_applied, _current_run.get() is run))
            seen.append(_current_run.get())
            return TurnResult()

    config = runtime_config("u")
    with patch.object(runtime, "get_service", return_value=Service()):
        futures = [
            runtime.submit_after_turn(
                runtime_config(f"u{i}"), Turn(f"u{i}", "s", "m", "q", "a", now()), None
            )
            for i in range(6)
        ]
        assert runtime.wait_for_idle(5)
    assert config and all(f is not None for f in futures)
    # Nothing leaked in, counts did not accumulate across jobs, nothing was left behind.
    assert [s for s in seen if isinstance(s, tuple)] == [(None, 1, True)] * 6
    assert [s for s in seen if not isinstance(s, tuple)] == [None] * 6


def now():
    return timezone.now()


def runtime_config(user):
    from chat.V2.personalization.gate import PersonalizationConfig

    return PersonalizationConfig(
        mode="realtime",
        injection="always",
        source="override",
        override_refused=False,
        fixture_profile=None,
        store_user_id=user,
    )


@pytest.mark.django_db(transaction=True)
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_stopped_turn_submits_no_after_turn(honoured, agent, service):
    client = APIClient()

    def intercept(message, *args, **kwargs):
        if message == "Agent completed":
            result = client.post(
                "/api/v2/chat/cancel",
                {"userId": TOKEN, "sessionId": "s1", "messageId": "m1"},
                format="json",
            )
            assert result.data["status"] == "cancelled"

    with (
        patch("chat.V2.views.logger.info", side_effect=intercept),
        patch.object(runtime, "submit_after_turn") as submit,
    ):
        body = post(context=OVERRIDE)
    assert b"event: cancelled" in body
    submit.assert_not_called()
    assert not ChatMessage.objects.filter(role="assistant").exists()


@pytest.mark.django_db
def test_failed_turn_submits_no_after_turn(honoured, service):
    async def send(**kwargs):
        raise ValueError("model down")

    with (
        patch("chat.V2.views.get_agent_service", return_value=MagicMock(send_message=send)),
        patch.object(runtime, "submit_after_turn") as submit,
    ):
        post(context=OVERRIDE)
    submit.assert_not_called()


@pytest.mark.django_db
def test_fixture_profile_is_rendered_and_not_stored(honoured, agent, service):
    with patch.object(runtime, "submit_after_turn") as submit:
        post(context={**OVERRIDE, "personalizationProfile": {"hebrew_fluency": "basic", "x": "y"}})
    seen = agent.contexts[0]
    assert seen.profile_text == "- Hebrew reading level: basic"
    assert seen.profile_slots_used == ["hebrew_fluency"]
    assert seen.personalization_meta["personalized"] is True
    assert service.reads == []  # the stored profile is not read
    assert ProfileSlot.objects.count() == 0
    submit.assert_not_called()


METADATA_KEYS = {
    "personalized",
    "personalization_mode",
    "personalization_injection",
    "personalization_source",
    "personalization_override_refused",
    "slots_used",
    "slot_set_version",
    "profile_revision",
    "profile_text",
    "personalization_model",
}


def logged(context):
    span = MagicMock()
    BraintrustTraceLogger().log_input(bt_span=span, user_message="hi", context=context, model="m")
    return span.log.call_args.kwargs["metadata"]


@pytest.mark.django_db
def test_trace_metadata_fields(honoured, agent, service):
    post("m1", context=OVERRIDE)
    metadata = logged(agent.contexts[0])
    assert METADATA_KEYS <= set(metadata)
    assert set(metadata) - METADATA_KEYS == {"model", "session_id", "user_id", "turn_number"} | {
        k for k in ("origin",) if k in metadata
    }
    assert metadata["personalized"] is True
    assert metadata["personalization_source"] == "override"
    assert metadata["personalization_mode"] == "realtime"
    assert metadata["personalization_override_refused"] is False
    assert metadata["slots_used"] == ["hebrew_fluency"]
    assert metadata["profile_revision"] == 3 and metadata["slot_set_version"] == "v1"
    assert metadata["profile_text"] == "- Hebrew reading level: basic"


@pytest.mark.django_db
def test_trace_metadata_for_off_and_refused(plain, agent):
    post("m1")
    off = logged(agent.contexts[0])
    assert {
        k for k in off if k.startswith("personaliz") or k in ("slots_used", "profile_text")
    } == {
        "personalized",
        "personalization_source",
    }
    post("m2", context=OVERRIDE)
    refused = logged(agent.contexts[1])
    assert refused["personalization_override_refused"] is True
    assert refused["personalized"] is False and refused["personalization_source"] == "off"
    assert "profile_text" not in refused


def claim(anon_id):
    return APIClient().post(
        "/api/v2/anon/claim", {"userId": TOKEN, "anonId": str(anon_id)}, format="json"
    )


def anon_session(anon_id):
    from chat.auth.anonymous import anonymous_user_id

    anon = anonymous_user_id(anon_id)
    ChatSession.objects.create(session_id="anon-s", user_id=anon)
    return anon


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET, PERSONALIZATION_MODE="explicit")
def test_claim_moves_profile():
    anon_id = uuid.uuid4()
    anon = anon_session(anon_id)
    store = DjangoProfileStore()
    assert store.set_stated(anon, "preferred_language", "he", now()).applied
    response = claim(anon_id)
    assert response.status_code == 200 and response.data["claimedSessions"] == 1
    assert store.get_profile(USER_ID)["preferred_language"].value == "he"
    assert store.get_profile(anon) == {}


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET, PERSONALIZATION_MODE="explicit")
def test_claim_survives_move_failure():
    anon_id = uuid.uuid4()
    anon_session(anon_id)

    def broken_move(self, from_user_id, to_user_id):
        with connection.cursor() as cursor:
            cursor.execute("select * from table_that_does_not_exist")

    with (
        patch.object(DjangoProfileStore, "move", broken_move),
        patch.object(runtime, "capture_exception") as capture,
    ):
        response = claim(anon_id)
    assert response.status_code == 200 and response.data["claimedSessions"] == 1
    assert ChatSession.objects.get(session_id="anon-s").user_id == USER_ID
    capture.assert_called_once()


@pytest.mark.django_db
@override_settings(CHATBOT_USER_TOKEN_SECRET=SECRET)
def test_claim_does_nothing_with_personalization_off():
    anon_id = uuid.uuid4()
    anon_session(anon_id)
    with patch.object(runtime, "get_store") as get_store:
        assert claim(anon_id).status_code == 200
    get_store.assert_not_called()


# -- lab compare: the plain arm's session is hidden only on a lab deployment ----------------------

COMPARE = {"personalizationMode": "off", "labCompare": True}


def listed_sessions():
    response = APIClient().get("/api/history/conversations", {"userId": TOKEN})
    assert response.status_code == 200
    return [c["sessionId"] for c in response.json()["conversations"]]


@pytest.mark.django_db
def test_lab_compare_session_is_hidden_when_honoured(honoured, agent, service, monkeypatch):
    monkeypatch.setenv("PERSONALIZATION_LAB_SEED", "true")
    post(context=COMPARE, session="plain-arm")
    session = ChatSession.objects.get(session_id="plain-arm")
    assert session.is_deleted is True and session.deleted_at is not None
    assert "plain-arm" not in listed_sessions()


@pytest.mark.django_db
def test_lab_compare_is_ignored_without_the_lab_flag(honoured, agent, service, monkeypatch):
    monkeypatch.delenv("PERSONALIZATION_LAB_SEED", raising=False)
    post(context=COMPARE, session="plain-arm")
    session = ChatSession.objects.get(session_id="plain-arm")
    assert session.is_deleted is False and session.deleted_at is None
    assert "plain-arm" in listed_sessions()


@pytest.mark.django_db
def test_lab_compare_is_ignored_for_a_caller_without_the_override(
    plain, agent, service, monkeypatch
):
    monkeypatch.setenv("PERSONALIZATION_LAB_SEED", "true")
    post(context=COMPARE, session="plain-arm")
    assert ChatSession.objects.get(session_id="plain-arm").is_deleted is False


@pytest.mark.django_db
def test_lab_compare_is_ignored_when_personalization_is_on(honoured, agent, service, monkeypatch):
    monkeypatch.setenv("PERSONALIZATION_LAB_SEED", "true")
    post(context={"personalizationMode": "realtime", "labCompare": True}, session="p-arm")
    assert ChatSession.objects.get(session_id="p-arm").is_deleted is False


@pytest.mark.django_db
def test_hidden_session_is_not_claimed(honoured, monkeypatch):
    import uuid

    from chat.auth.anonymous import anonymous_user_id

    anon_id = uuid.uuid4()
    ChatSession.objects.create(
        session_id="hidden", user_id=anonymous_user_id(anon_id), is_deleted=True
    )
    assert claim(anon_id).json()["claimedSessions"] == 0
