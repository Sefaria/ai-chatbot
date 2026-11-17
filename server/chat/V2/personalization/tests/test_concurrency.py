"""Privacy guarantees under concurrency: each interleaving is forced by a fake model or Jev that
changes the store while the strategy waits for it, or by calling the store in the racing order."""

import json
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from chat.V2.personalization.jev import FakeJevClient, JevAnswer
from chat.V2.personalization.store import DjangoProfileStore
from chat.V2.personalization.types import Observation

from .conftest import NOW, day, evidence, make_strategy, make_turn
from .test_hybrid import INSTRUCTION
from .test_hybrid_jev import STYLE_TEXT, choice, jev_with
from .test_hybrid_jev import strategy as jev_strategy
from .test_nightly import Q1, Q2, clear_item, consolidation, log_turns, update

django_only = pytest.mark.django_db


def during_call(action, reply="{}"):
    """A model responder that runs `action()` (the racing writer) before it answers."""

    def respond(system, user):
        action()
        return reply(system, user) if callable(reply) else reply

    return respond


def two_session_update(slot="hebrew_fluency", value="basic"):
    return update(slot, value, ("m1", Q1), ("m2", Q2))


def run_nightly(store, registry, responder, name="nightly"):
    strategy, _, _ = make_strategy(name, store, registry, responder)
    log_turns(strategy, [("s1", "m1", Q1, day(-3)), ("s2", "m2", Q2, day(-1))])
    return strategy, strategy.run_batch(NOW)


# -- 2. set_inferred never writes over a slot that became stated -----------------------------


@django_only
def test_set_inferred_does_not_overwrite_a_slot_stated_after_the_lookup():
    from chat.models import ProfileSlot

    store, injected = DjangoProfileStore(), []
    original_first = type(ProfileSlot.objects.all()).first

    def first(queryset):
        row = original_first(queryset)
        if queryset.model is ProfileSlot and row is None and not injected:
            injected.append(
                True
            )  # the learner states the slot right after the lookup came up empty
            store.set_stated("a", "hebrew_fluency", "fluent", NOW)
        return row

    with patch.object(type(ProfileSlot.objects.all()), "first", first):
        refused = store.set_inferred("a", "hebrew_fluency", "none", [evidence()], day(1))
    assert refused.reason == "stated_wins"
    slot = store.get_profile("a")["hebrew_fluency"]
    assert (slot.value, slot.status) == ("fluent", "stated") and injected


# -- 3. a model-requested clear never deletes a slot stated during the call -------------------


def test_delete_slot_if_inferred_only_deletes_inferred(store):
    store.set_inferred("a", "answer_style", "Be brief.", [evidence()], NOW)
    store.set_stated("a", "hebrew_fluency", "fluent", NOW)
    assert store.delete_slot_if_inferred("a", "hebrew_fluency").reason == "stated_wins"
    missing = store.delete_slot_if_inferred("a", "learning_habits")
    assert (missing.applied, missing.reason) == (False, None)
    assert store.delete_slot_if_inferred("a", "answer_style").applied
    assert set(store.get_profile("a")) == {"hebrew_fluency"}


def test_clear_request_does_not_delete_a_slot_stated_during_the_model_call(store, registry):
    store.set_inferred("u1", "answer_style", "Be long.", [], day(-9))
    stating = lambda: store.set_stated("u1", "answer_style", "Be brief.", NOW)  # noqa: E731
    responder = during_call(stating, consolidation(clear=[clear_item("answer_style")]))
    run_nightly(store, registry, responder)
    slot = store.get_profile("u1")["answer_style"]
    assert (slot.value, slot.status) == ("Be brief.", "stated")


def test_clear_request_without_validated_evidence_is_ignored(store, registry):
    store.set_inferred("u1", "answer_style", "Be long.", [], day(-9))
    bad = [
        "answer_style",  # no evidence at all
        {"slot": "answer_style", "evidence": []},
        clear_item("answer_style", quote="words the learner never wrote"),
    ]
    _, result = run_nightly(store, registry, consolidation(clear=bad))
    assert "answer_style" in store.get_profile("u1")
    assert result.diagnostics.get("cleared", 0) == 0


# -- 4. an in-flight job cannot write after clear() or move() --------------------------------


def test_clear_and_move_bump_the_revision_and_refuse_older_writes(store):
    before = store.revision("a")
    store.clear("a")
    assert store.revision("a") == before + 1
    stale = store.set_inferred("a", "hebrew_fluency", "basic", [evidence()], NOW, None, before)
    assert stale.reason == "stale_revision"
    late = store.add_proposal("a", "answer_style", "x", "q", NOW, expected_revision=before)
    assert late.proposal is None and late.reason == "stale_revision"
    assert store.delete_slot_if_inferred("a", "answer_style", before).reason == "stale_revision"
    assert store.set_inferred(
        "a", "hebrew_fluency", "basic", [evidence()], NOW, None, before + 1
    ).applied
    first, second = store.revision("a"), store.revision("b")
    store.move("a", "b")
    assert store.revision("a") == first + 1 and store.revision("b") == second + 1
    moved = store.set_inferred("b", "answer_style", "x", [evidence()], NOW, None, second)
    assert moved.reason == "stale_revision"


def test_nightly_clear_during_the_model_call_leaves_nothing_behind(store, registry):
    responder = during_call(lambda: store.clear("u1"), consolidation(two_session_update()))
    _, result = run_nightly(store, registry, responder)
    assert store.get_profile("u1") == {} and store.observations("u1") == []
    assert result.diagnostics["stale_revision"] == 1 and result.users_processed == 0


def test_realtime_clear_during_the_model_call_writes_nothing(store, registry):
    reply = json.dumps(
        {"updates": [{"slot": "hebrew_fluency", "value": "basic", "quote": "I read Hebrew slowly"}]}
    )
    strategy, _, _ = make_strategy(
        "realtime", store, registry, during_call(lambda: store.clear("u1"), reply)
    )
    result = strategy.on_turn(make_turn(Q3_TEXT), NOW)
    assert "hebrew_fluency" not in store.get_profile("u1")
    assert result.diagnostics["stale_revision"] == 1


Q3_TEXT = "I read Hebrew slowly"


def test_hybrid_proposal_after_a_clear_during_the_call_is_dropped(store, registry):
    reply = json.dumps(
        {"slot": "answer_style", "value": "Keep answers short.", "excludes_sources": False}
    )
    strategy, _, _ = make_strategy(
        "hybrid", store, registry, during_call(lambda: store.clear("u1"), reply)
    )
    result = strategy.on_turn(make_turn(INSTRUCTION), NOW)
    assert result.proposals == [] and store.proposals("u1") == [] and store.observations("u1") == []
    assert result.diagnostics["stale_revision"] == 1


def test_hybrid_jev_clear_during_the_jev_call_writes_nothing(store, registry):
    def respond(state, questions):
        store.clear("u1")
        return {"hebrew_fluency": choice("basic", 0.9)}

    s, _, _ = jev_strategy(store, registry, FakeJevClient(respond))
    result = s.on_turn(make_turn("Explain Berakhot"), NOW)
    assert store.get_profile("u1") == {} and store.observations("u1") == []
    assert result.diagnostics["stale_revision"] == 1


def test_move_during_the_model_call_invalidates_the_job(store, registry):
    responder = during_call(lambda: store.move("u1", "u2"), consolidation(two_session_update()))
    _, result = run_nightly(store, registry, responder)
    assert "hebrew_fluency" not in store.get_profile("u1")
    assert result.diagnostics["stale_revision"] == 1 and result.users_processed == 0


# -- 5. consent is re-checked at commit time -------------------------------------------------


def test_store_enabled_is_checked_at_commit_by_the_store(store):
    store.set_settings("a", store_enabled=False)
    results = [
        store.set_inferred("a", "hebrew_fluency", "basic", [evidence()], NOW),
        store.set_stated("a", "hebrew_fluency", "basic", NOW),
        store.add_proposal("a", "answer_style", "x", "q", NOW),
        store.add_observation(Observation("a", "s", "m", "hi", NOW)),
    ]
    assert [(r.applied, r.reason) for r in results] == [(False, "store_disabled")] * 4
    assert store.get_profile("a") == {} and store.observations("a") == []
    assert store.proposals("a") == []


def disable(store):
    return lambda: store.set_settings("u1", store_enabled=False)


def test_nightly_store_disabled_during_the_call_stores_nothing(store, registry):
    responder = during_call(disable(store), consolidation(two_session_update()))
    _, result = run_nightly(store, registry, responder)
    assert "hebrew_fluency" not in store.get_profile("u1")
    assert result.diagnostics["store_disabled"] >= 1


def test_realtime_store_disabled_during_the_call_stores_nothing(store, registry):
    reply = json.dumps(
        {"updates": [{"slot": "hebrew_fluency", "value": "basic", "quote": Q3_TEXT}]}
    )
    strategy, _, _ = make_strategy("realtime", store, registry, during_call(disable(store), reply))
    result = strategy.on_turn(make_turn(Q3_TEXT), NOW)
    assert "hebrew_fluency" not in store.get_profile("u1")
    assert result.diagnostics["store_disabled"] >= 1


def test_hybrid_store_disabled_during_the_call_stores_nothing(store, registry):
    reply = json.dumps(
        {"slot": "answer_style", "value": "Keep answers short.", "excludes_sources": False}
    )
    strategy, _, _ = make_strategy("hybrid", store, registry, during_call(disable(store), reply))
    result = strategy.on_turn(make_turn(INSTRUCTION), NOW)
    assert result.proposals == [] and store.proposals("u1") == []
    (marker,) = store.observations("u1", include_quarantined=True)  # logged before the call
    assert marker.text == "" and marker.quarantined
    assert result.diagnostics["store_disabled"] >= 1


def test_hybrid_jev_store_disabled_during_the_call_stores_nothing(store, registry):
    def respond(state, questions):
        disable(store)()
        return {"hebrew_fluency": choice("basic", 0.9)}

    s, _, _ = jev_strategy(store, registry, FakeJevClient(respond))
    result = s.on_turn(make_turn("Explain Berakhot"), NOW)
    # only the code-set language, from before
    assert "hebrew_fluency" not in store.get_profile("u1")
    assert result.diagnostics["store_disabled"] >= 1


def test_hybrid_jev_proposal_is_refused_when_storage_is_disabled_during_the_call(store, registry):
    def respond(state, questions):
        disable(store)()
        return {
            "is_lasting_style_preference": JevAnswer("noul", noul=0.95),
            "asks_to_exclude_sources": JevAnswer("noul", noul=0.0),
        }

    s, _, _ = jev_strategy(store, registry, FakeJevClient(respond))
    result = s.on_turn(make_turn(STYLE_TEXT), NOW)
    assert result.proposals == [] and store.proposals("u1") == []
    assert result.diagnostics["store_disabled"] >= 1


# -- 6. only the observations a batch loaded are consumed ------------------------------------


def test_mark_consumed_takes_only_the_given_ids(store):
    for n in (1, 2):
        store.add_observation(Observation("a", "s", f"m{n}", "hi", NOW))
    first, second = store.observations("a")
    assert store.mark_consumed("a", [first.id], NOW).applied
    assert [o.consumed_at for o in store.observations("a")] == [NOW, None]
    assert second.id != first.id and store.users_with_pending() == ["a"]


def test_a_turn_arriving_during_consolidation_makes_the_batch_retry_later(store, registry):
    arriving = lambda: store.add_observation(  # noqa: E731
        Observation("u1", "s3", "m_new", "a brand new prompt", NOW)
    )
    strategy, result = run_nightly(store, registry, during_call(arriving, consolidation()))
    # The new turn bumped the revision: this run's input is stale, nothing is consumed.
    assert result.users_processed == 0 and result.diagnostics["stale_revision"] == 1
    assert all(o.consumed_at is None for o in store.observations("u1"))
    assert store.users_with_pending() == ["u1"]
    strategy, _, _ = make_strategy("nightly", store, registry, consolidation())
    assert strategy.run_batch(NOW).users_processed == 1
    assert store.users_with_pending() == []


# -- 7. resolve_proposal -----------------------------------------------------------------------


def test_second_resolve_of_a_proposal_does_nothing(store):
    proposal = store.add_proposal("a", "answer_style", "Be brief.", "q", NOW).proposal
    assert store.resolve_proposal("a", proposal.id, True, NOW).proposal.status == "accepted"
    assert not store.resolve_proposal("a", proposal.id, False, day(1)).applied
    assert store.proposals("a")[0].status == "accepted"
    slot = store.get_profile("a")["answer_style"]
    assert (slot.value, slot.status) == ("Be brief.", "stated")


@django_only
def test_resolve_proposal_locks_the_row_before_reading_its_status():
    store = DjangoProfileStore()
    proposal = store.add_proposal("a", "answer_style", "Be brief.", "q", NOW).proposal
    with CaptureQueriesContext(connection) as queries:
        store.resolve_proposal("a", proposal.id, True, NOW)
    sql = [q["sql"] for q in queries]
    locking = [i for i, s in enumerate(sql) if "FOR UPDATE" in s and "chat_profileproposal" in s]
    updating = [i for i, s in enumerate(sql) if s.startswith("UPDATE") and "proposal" in s]
    assert locking and updating and locking[0] < updating[0]


@django_only
def test_proposal_status_and_slot_write_roll_back_together():
    store = DjangoProfileStore()
    proposal = store.add_proposal("a", "answer_style", "Be brief.", "q", NOW).proposal
    with patch.object(store, "_do_set_stated", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            store.resolve_proposal("a", proposal.id, True, NOW)
    assert store.proposals("a")[0].status == "pending" and store.get_profile("a") == {}


# -- 8. move -----------------------------------------------------------------------------------


def test_move_target_stated_slot_wins_and_newer_inferred_value_wins(store):
    store.set_stated("to", "hebrew_fluency", "fluent", day(-5))
    store.set_inferred("from", "hebrew_fluency", "none", [evidence()], day(-1))
    store.set_inferred("to", "answer_style", "Old.", [evidence()], day(-3))
    store.set_inferred("from", "answer_style", "New.", [evidence()], day(-1))
    store.set_inferred("from", "learning_habits", "Evenings.", [evidence()], day(-1))
    store.move("from", "to")
    profile = store.get_profile("to")
    assert (profile["hebrew_fluency"].value, profile["hebrew_fluency"].status) == (
        "fluent",
        "stated",
    )
    assert profile["answer_style"].value == "New." and profile["learning_habits"].value
    assert store.get_profile("from") == {}


@django_only
def test_move_locks_both_settings_rows_in_sorted_order_before_reading_slots():
    store = DjangoProfileStore()
    store.set_inferred("zed", "answer_style", "x", [evidence()], NOW)
    with CaptureQueriesContext(connection) as queries:
        store.move("zed", "abe")  # the source sorts after the target
    sql = [q["sql"] for q in queries]
    lock = next(i for i, s in enumerate(sql) if "FOR UPDATE" in s and "chat_profilesettings" in s)
    slots = next(i for i, s in enumerate(sql) if "chat_profileslot" in s and "SELECT" in s)
    assert lock < slots and "ORDER BY" in sql[lock] and "IN ('abe', 'zed')" in sql[lock]


@django_only
def test_move_keeps_the_source_settings_row_so_its_revision_survives():
    store = DjangoProfileStore()
    store.set_settings("from", store_enabled=False)  # revision 1
    store.move("from", "to")  # revision 2
    assert not store.get_settings("to").store_enabled  # the opt-out follows the learner
    assert store.revision("from") == 2 and store.get_settings("from").store_enabled


# -- 9. set_settings updates only what it is given ----------------------------------------------


def test_set_settings_changes_only_the_toggles_passed(store):
    store.set_settings("a", personalize_enabled=False)
    store.set_settings("a", store_enabled=False)
    store.set_settings("a")
    settings = store.get_settings("a")
    assert (settings.personalize_enabled, settings.store_enabled) == (False, False)


# -- 10. realtime never sends the assistant reply --------------------------------------------


def test_realtime_never_sends_the_assistant_reply(store, registry):
    strategy, llm, _ = make_strategy("realtime", store, registry)
    reply = "You mentioned your cancer diagnosis earlier."
    strategy.on_turn(make_turn("Please continue", reply=reply), NOW)
    assert llm.calls and "cancer" not in llm.calls[0]["user"] + llm.calls[0]["system"]
    assert "Assistant" not in llm.calls[0]["user"]


# -- misc: jev_with kept for symmetry ----------------------------------------------------------


def test_hybrid_jev_helper_is_importable():
    assert jev_with() is not None
