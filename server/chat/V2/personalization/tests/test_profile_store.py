"""The Profile store: settings, sections, runs, prompts. Made-up text only."""

from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone

from chat.models import ChatMessage, ChatSession, ProfileWriterRun
from chat.V2.personalization import profile_store as store
from chat.V2.personalization.profile_store import SectionData

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def section(heading, body, *, key=None, kind="core", origin="inferred", position=0):
    return SectionData(
        key=key,
        kind=kind,
        heading=heading,
        body=body,
        origin=origin,
        last_supported_at=NOW,
        position=position,
    )


_counter = iter(range(10_000))


def add_prompt(user, text, *, session="s1", at=None, **extra):
    n = next(_counter)
    # A finished turn by default; pass processing_state="" for a message saved a moment ago.
    extra.setdefault("processing_state", ChatMessage.ProcessingState.COMPLETED)
    msg = ChatMessage.objects.create(
        message_id=f"m{n}",
        session_id=session,
        user_id=user,
        turn_id=extra.pop("turn_id", f"t{n}"),
        role=ChatMessage.Role.USER,
        content=text,
        **extra,
    )
    if at is not None:
        ChatMessage.objects.filter(pk=msg.pk).update(server_timestamp=at)
    return msg


def test_defaults():
    s = store.get_settings("u1")
    assert s.instructions_enabled is True and s.profile_enabled is True
    assert s.instructions_text == "" and s.profile_revision == 0
    assert s.profile_updated_at is None and s.instructions_updated_at is None


def test_instructions_cap():
    assert store.set_instructions("u1", "x" * 300).instructions_text == "x" * 300
    with pytest.raises(ValueError, match="too_long"):
        store.set_instructions("u1", "x" * 301)
    assert store.set_instructions("u1", "ש" * 300).instructions_text == "ש" * 300
    with pytest.raises(ValueError, match="too_long"):
        store.set_instructions("u1", "ש" * 301)
    assert store.get_settings("u1").instructions_text == "ש" * 300


def test_set_instructions_changes_the_date_only_when_the_text_changes():
    empty = store.set_instructions("u1", "")
    assert empty.changed is False and empty.instructions_updated_at is None
    first = store.set_instructions("u1", "  be brief ")
    assert first.changed is True and first.instructions_text == "be brief"
    stamp = first.instructions_updated_at
    assert stamp is not None
    same = store.set_instructions("u1", "be brief  ")
    assert same.changed is False and same.instructions_updated_at == stamp
    assert store.get_settings("u1").instructions_updated_at == stamp
    cleared = store.set_instructions("u1", "")
    assert cleared.changed is True and cleared.instructions_text == ""
    assert cleared.instructions_updated_at is not None and cleared.instructions_updated_at >= stamp


def test_set_enabled_reports_whether_it_changed():
    assert store.set_enabled("u1", "profile", True).changed is False
    assert store.set_enabled("u1", "profile", False).changed is True
    assert store.set_enabled("u1", "profile", False).changed is False


def test_set_enabled_and_testing():
    assert store.set_enabled("u1", "profile", False).profile_enabled is False
    assert store.set_enabled("u1", "instructions", False).instructions_enabled is False
    with pytest.raises(ValueError):
        store.set_enabled("u1", "other", True)
    t = store.set_testing("u1", show_plain_response=True)
    assert t.show_plain_response is True and t.update_every_message is False


def test_replace_sections_revision_guard():
    assert store.replace_sections(
        "u1", 0, [section("A", "a")], updated_by="la", processed_through=None, set_notice=False
    )
    assert not store.replace_sections(
        "u1", 0, [section("B", "b")], updated_by="la", processed_through=None, set_notice=True
    )
    assert [s.heading for s in store.get_sections("u1")] == ["A"]
    after = store.get_settings("u1")
    assert after.profile_revision == 1 and after.notice_pending is False


def test_replace_sections_bumps():
    through = NOW
    assert store.replace_sections(
        "u1",
        0,
        [section("A", "a"), section("B", "b")],
        updated_by="you_la",
        processed_through=through,
        set_notice=True,
    )
    s = store.get_settings("u1")
    assert s.profile_revision == 1
    assert s.profile_updated_by == "you_la" and s.profile_updated_at is not None
    assert s.processed_through == through and s.notice_pending is True
    assert [x.position for x in store.get_sections("u1")] == [0, 1]


def test_clear():
    user = "u1"
    store.replace_sections(
        user, 0, [section("A", "a")], updated_by="la", processed_through=None, set_notice=False
    )
    run_id = store.record_run(
        user_id=user,
        trigger="idle",
        status="applied",
        extraction="made-up extraction",
        profile_before="# A\nold",
        profile_after="# A\nnew",
    )
    other = store.record_run(user_id="u2", trigger="idle", status="applied", profile_after="keep")
    old = timezone.now() - timedelta(hours=1)
    add_prompt(user, "an older made-up question", at=old)

    cleared = store.clear_profile(user)

    assert store.get_sections(user) == []
    assert cleared.cleared_at is not None and cleared.processed_through == cleared.cleared_at
    run = ProfileWriterRun.objects.get(pk=run_id)
    assert (run.profile_before, run.profile_after, run.extraction) == ("", "", "")
    assert ProfileWriterRun.objects.get(pk=other).profile_after == "keep"
    assert store.unprocessed_user_prompts(user, None) == []
    assert store.unprocessed_user_prompts(user, old - timedelta(days=1)) == []
    # A prompt after the clear is read.
    fresh = add_prompt(user, "a newer made-up question")
    assert [p.message_id for p in store.unprocessed_user_prompts(user, None)] == [fresh.message_id]


def test_prompts_skip_hidden_sessions():
    ChatSession.objects.create(session_id="hidden", user_id="u1", is_deleted=True)
    ChatSession.objects.create(session_id="shown", user_id="u1")
    add_prompt("u1", "plain arm question", session="hidden")
    kept = add_prompt("u1", "shown question", session="shown")
    assert [p.message_id for p in store.unprocessed_user_prompts("u1", None)] == [kept.message_id]


def test_prompts_skip_failed_cancelled_and_refused_turns():
    kept = add_prompt("u1", "answered question", processing_state="completed")
    add_prompt("u1", "failed question", processing_state="failed")
    add_prompt("u1", "cancelled question", processing_state="cancelled")
    refused = add_prompt("u1", "refused question")
    answer = ChatMessage.objects.create(
        message_id="a-ref",
        session_id="s1",
        user_id="u1",
        turn_id=refused.turn_id,
        role=ChatMessage.Role.ASSISTANT,
        content="no",
        status=ChatMessage.Status.REFUSED,
    )
    ChatMessage.objects.filter(pk=refused.pk).update(response_message=answer)
    errored = add_prompt("u1", "errored question")
    ChatMessage.objects.create(
        message_id="a-err",
        session_id="s1",
        user_id="u1",
        turn_id=errored.turn_id,
        role=ChatMessage.Role.ASSISTANT,
        content="oops",
        status=ChatMessage.Status.FAILED,
    )
    assert [p.message_id for p in store.unprocessed_user_prompts("u1", None)] == [kept.message_id]


def test_prompts_window_and_order():
    a = add_prompt("u1", "first", at=NOW)
    b = add_prompt("u1", "second", at=NOW + timedelta(minutes=1))
    c = add_prompt("u1", "third", at=NOW + timedelta(minutes=2))
    got = store.unprocessed_user_prompts("u1", NOW, NOW + timedelta(minutes=1))
    assert [p.message_id for p in got] == [b.message_id]
    all_ = store.unprocessed_user_prompts("u1", None)
    assert [p.message_id for p in all_] == [a.message_id, b.message_id, c.message_id]


def test_isolation():
    store.set_instructions("a", "only for a")
    store.replace_sections(
        "a", 0, [section("A", "mine")], updated_by="la", processed_through=None, set_notice=True
    )
    add_prompt("a", "a's question")
    assert store.get_settings("b").instructions_text == ""
    assert store.get_sections("b") == []
    assert store.unprocessed_user_prompts("b", None) == []
    assert store.take_notice("b") is False
    store.clear_profile("b")
    store.set_enabled("b", "profile", False)
    assert [s.heading for s in store.get_sections("a")] == ["A"]
    assert store.get_settings("a").profile_enabled is True
    assert store.get_settings("a").instructions_text == "only for a"


def test_render_profile():
    text = store.render_profile([section("A", "body a"), section("B", "body b")])
    assert text == "# A\nbody a\n\n# B\nbody b"
    assert store.render_profile([]) == ""


def test_take_notice_once():
    store.replace_sections(
        "u1", 0, [section("A", "a")], updated_by="la", processed_through=None, set_notice=True
    )
    assert store.take_notice("u1") is True
    assert store.take_notice("u1") is False
