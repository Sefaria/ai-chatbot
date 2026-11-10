"""Retention, bounded queries, move precedence and screening of stated values."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from chat.V2.personalization.service import PersonalizationService
from chat.V2.personalization.signals import KeywordSensitiveScreen
from chat.V2.personalization.store import DjangoProfileStore
from chat.V2.personalization.types import Observation

from .conftest import NOW, day, evidence, make_strategy

SENSITIVE = "Be gentle, I am sitting shiva for my father"


def obs(message, at, user="u1"):
    return Observation(user, "s1", message, f"text {message}", at, {})


def _service(store, registry):
    strategy, _, _ = make_strategy("explicit", store, registry)
    return PersonalizationService(store, registry, strategy, mode="explicit")


def test_keyword_screen_wraps_the_sensitive_filter():
    screen = KeywordSensitiveScreen()
    assert screen.is_sensitive(SENSITIVE)
    assert not screen.is_sensitive("Explain Berakhot 2a")


def test_sensitive_interview_answer_is_refused(store, registry):
    service = _service(store, registry)
    written = service.apply_interview(
        "u1", {"answer_style": SENSITIVE, "hebrew_fluency": "basic"}, NOW
    )
    assert written == ["hebrew_fluency"]
    assert "answer_style" not in store.get_profile("u1")


def test_sensitive_stated_value_is_refused(store, registry):
    service = _service(store, registry)
    result = service.set_stated("u1", "answer_style", SENSITIVE, NOW)
    assert (result.applied, result.reason) == (False, "sensitive")
    assert store.get_profile("u1") == {}
    assert service.set_stated("u1", "answer_style", "Be brief.", NOW).applied
    assert store.get_profile("u1")["answer_style"].status == "stated"


def test_purge_deletes_only_older_than_30_days(store):
    store.add_observation(obs("old", day(-31)))
    store.add_observation(obs("edge", day(-29)))
    store.add_observation(obs("other", day(-40), user="u2"))
    assert store.purge_observations(day(-30)) == 2
    assert [o.message_id for o in store.observations("u1")] == ["edge"]
    assert store.observations("u2") == []


def test_observations_limit_keeps_newest_returned_oldest_first(store):
    for n in range(5):
        store.add_observation(obs(f"m{n}", day(-n)))
    store.add_observation(Observation("u1", "s1", "q", "", day(0), {}, quarantined=True))
    got = store.observations("u1", since=day(-3), limit=2)
    assert [o.message_id for o in got] == ["m1", "m0"]
    assert [o.message_id for o in store.observations("u1", since=day(-3))] == [
        "m3", "m2", "m1", "m0",
    ]  # fmt: skip
    assert len(store.observations("u1", since=day(-3), limit=2, include_quarantined=True)) == 2


@pytest.mark.django_db
def test_observation_window_is_bounded_in_sql():
    store = DjangoProfileStore()
    for n in range(5):
        store.add_observation(obs(f"m{n}", day(-n)))
    with CaptureQueriesContext(connection) as ctx:
        got = store.observations("u1", since=day(-4), limit=2)
    assert len(got) == 2
    assert len(ctx.captured_queries) == 1
    assert "LIMIT" in ctx.captured_queries[0]["sql"]


@pytest.mark.django_db
def test_expire_user_is_one_delete():
    store = DjangoProfileStore()
    store.set_inferred("u1", "hebrew_fluency", "basic", [evidence()], day(-5), expires_at=day(-1))
    with CaptureQueriesContext(connection) as ctx:
        assert store.expire_user("u1", NOW).applied
    deletes = [q["sql"] for q in ctx.captured_queries if q["sql"].lstrip().startswith("DELETE")]
    assert len(deletes) == 1 and "expires_at" in deletes[0]
    assert store.get_profile("u1") == {}


def test_move_stated_on_anonymous_side_beats_newer_inferred(store):
    store.set_stated("anon", "hebrew_fluency", "fluent", day(-5))
    store.set_inferred("user", "hebrew_fluency", "none", [evidence()], day(-1))
    store.move("anon", "user")
    slot = store.get_profile("user")["hebrew_fluency"]
    assert (slot.value, slot.status) == ("fluent", "stated")


def test_move_stated_on_both_sides_target_wins_and_inferred_newer_wins(store):
    store.set_stated("anon", "hebrew_fluency", "fluent", day(-1))
    store.set_stated("user", "hebrew_fluency", "basic", day(-9))
    store.set_inferred("anon", "answer_style", "New.", [evidence()], day(-1))
    store.set_inferred("user", "answer_style", "Old.", [evidence()], day(-3))
    store.move("anon", "user")
    profile = store.get_profile("user")
    assert profile["hebrew_fluency"].value == "basic"
    assert profile["answer_style"].value == "New."
