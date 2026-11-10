"""One per-user lock and one revision for every profile write.

Every mutating store method takes the user's lock once, refuses with `store_disabled` or
`stale_revision` before it runs, and adds 1 to the user's revision when it applies. Strategies
read the revision once before a model or Jev call and carry each applied write's revision forward.
"""

import json
import threading

import pytest
from django.db import connections

from chat.V2.personalization.jev import FakeJevClient, JevAnswer
from chat.V2.personalization.store import DjangoProfileStore, InMemoryProfileStore
from chat.V2.personalization.types import Observation, WriteResult

from .conftest import NOW, day, evidence, make_strategy, make_turn
from .test_nightly import Q1, Q2, consolidation, log_turns, update

MODES = ["realtime", "nightly", "hybrid", "hybrid_jev", "explicit"]


@pytest.fixture(
    params=["memory", pytest.param("django", marks=pytest.mark.django_db(transaction=True))]
)
def tstore(request):
    """Both stores, with real transactions so threads see each other's commits."""
    if request.param == "memory":
        return InMemoryProfileStore()
    return DjangoProfileStore()


def run_threads(*targets, timeout=5.0):
    """Run each target in its own thread; fail on a hang (deadlock) or an exception."""
    errors: list[BaseException] = []

    def wrap(target):
        def run():
            try:
                target()
            except BaseException as exc:  # noqa: BLE001 - reported to the test below
                errors.append(exc)
            finally:
                connections.close_all()

        return run

    threads = [threading.Thread(target=wrap(t), daemon=True) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
    assert not any(t.is_alive() for t in threads), "a writer is stuck: deadlock"
    assert not errors, errors


def jev_plain():
    def respond(state, questions):
        if "message" in state:
            return {
                "is_lasting_style_preference": JevAnswer("noul", noul=0.0),
                "asks_to_exclude_sources": JevAnswer("noul", noul=0.0),
            }
        return {"category:Talmud": JevAnswer("noul", noul=0.9)}

    return FakeJevClient(respond)


def strategy_for(mode, store, registry, responder=lambda s, u: "{}"):
    jev = jev_plain() if mode == "hybrid_jev" else None
    strategy, llm, _ = make_strategy(mode, store, registry, responder, jev=jev)
    return strategy, llm, jev


# -- R2-7: an older, slower extraction cannot overwrite a newer one ----------------------------


def test_slow_older_extraction_does_not_overwrite_newer(store):
    for _ in range(3):
        store.set_settings("u1", personalize_enabled=True)
    assert store.revision("u1") == 3
    a_read = store.revision("u1")
    b_read = store.revision("u1")
    b = store.set_inferred(
        "u1", "hebrew_fluency", "fluent", [evidence()], day(1), expected_revision=b_read
    )
    assert b.applied and b.revision == 4
    a = store.set_inferred(
        "u1", "hebrew_fluency", "none", [evidence()], NOW, expected_revision=a_read
    )
    assert (a.applied, a.reason, a.revision) == (False, "stale_revision", 4)
    assert store.get_profile("u1")["hebrew_fluency"].value == "fluent"


# -- R2-1: clear racing an accepted proposal never leaves a slot behind -------------------------


def test_clear_racing_accept_leaves_no_slot(tstore):
    for i in range(50):
        user = f"race{i}"
        proposal = tstore.add_proposal(user, "answer_style", "Be brief.", "q", NOW).proposal
        run_threads(
            lambda u=user: tstore.clear(u),
            lambda u=user, p=proposal: tstore.resolve_proposal(u, p.id, True, NOW),
        )
        assert tstore.get_profile(user) == {}, f"round {i}: a slot survived the clear"
        assert tstore.proposals(user) == []


# -- R2-2: a hot-path observation after a clear is refused as stale -----------------------------


def test_hot_path_observation_after_clear_is_refused_when_stale(store, registry, monkeypatch):
    strategy, _, _ = make_strategy("nightly", store, registry)
    real_revision = store.revision

    def revision_then_clear(user_id):
        value = real_revision(user_id)
        store.clear(user_id)  # the learner clears memory right after the turn read its revision
        return value

    monkeypatch.setattr(store, "revision", revision_then_clear)
    result = strategy.on_turn(make_turn("Explain Berakhot"), NOW)
    assert store.observations("u1") == []
    assert result.diagnostics.get("stale_revision") == 1
    assert result.writes_refused == {"stale_revision": 1}


# -- R2-8: consent is enforced for stated writes too --------------------------------------------


def test_accept_after_store_disabled_creates_no_slot(store):
    proposal = store.add_proposal("u1", "answer_style", "Be brief.", "q", NOW).proposal
    store.set_settings("u1", store_enabled=False)
    result = store.resolve_proposal("u1", proposal.id, True, NOW)
    assert (result.applied, result.reason) == (False, "store_disabled")
    assert store.get_profile("u1") == {}
    assert store.set_stated("u1", "hebrew_fluency", "fluent", NOW).reason == "store_disabled"
    assert store.get_profile("u1") == {}


def test_interview_after_store_disabled_writes_nothing(store, registry):
    strategy, _, _ = make_strategy("explicit", store, registry)
    store.set_settings("u1", store_enabled=False)
    assert strategy.apply_interview("u1", {"hebrew_fluency": "fluent"}, NOW) == []
    assert store.get_profile("u1") == {}


# -- R2-9: a proposal created while the batch waits blocks the batch's write --------------------


def test_proposal_created_during_model_call_blocks_batch_write(store, registry):
    store.set_inferred("u1", "hebrew_fluency", "none", [evidence()], day(-9))

    def respond(system, user):
        store.add_proposal("u1", "hebrew_fluency", "fluent", "I am fluent", NOW)
        return consolidation(update("hebrew_fluency", "basic", ("m1", Q1), ("m2", Q2)))(
            system, user
        )

    strategy, _, _ = make_strategy("hybrid", store, registry, respond)
    log_turns(strategy, [("s1", "m1", Q1, day(-3)), ("s2", "m2", Q2, day(-1))])
    result = strategy.run_batch(NOW)
    assert store.get_profile("u1")["hebrew_fluency"].value == "none"
    assert {"pending_proposal", "stale_revision"} & set(result.diagnostics)
    # Checked inside the lock even without a revision.
    refused = store.set_inferred("u1", "hebrew_fluency", "basic", [evidence()], NOW)
    assert (refused.applied, refused.reason) == (False, "pending_proposal")


# -- R2-10: concurrent identical proposals insert one row ---------------------------------------


def test_concurrent_add_proposal_inserts_one(tstore):
    barrier = threading.Barrier(8, timeout=5)

    def propose():
        barrier.wait()
        tstore.add_proposal("u1", "answer_style", "Be brief.", "keep it short", NOW)

    run_threads(*[propose] * 8)
    assert len(tstore.proposals("u1", "pending")) == 1


# -- R2-11: a replayed turn is observed once ----------------------------------------------------


def test_replayed_turn_is_observed_once(store):
    first = store.add_observation(Observation("u1", "s1", "m1", "hello", NOW))
    assert first.applied
    again = store.add_observation(Observation("u1", "s1", "m1", "hello", NOW))
    assert (again.applied, again.reason, again.revision) == (False, "duplicate", first.revision)
    assert store.revision("u1") == first.revision
    assert len(store.observations("u1")) == 1


# -- every applied write bumps the revision by exactly one --------------------------------------


def _slot(store):
    store.set_inferred("u1", "answer_style", "Be brief.", [evidence()], NOW)


def _stated(store):
    store.set_stated("u1", "hebrew_fluency", "fluent", NOW)


def _expired(store):
    store.set_inferred("u1", "text_categories", ["Talmud"], [evidence()], day(-70), day(-10))


def _observed(store):
    store.add_observation(Observation("u1", "s1", "m1", "hello", NOW))


def _proposed(store):
    return store.add_proposal("u1", "answer_style", "Be brief.", "q", NOW).proposal


WRITES = {
    "set_inferred": (None, lambda s, _: s.set_inferred("u1", "hebrew_fluency", "basic", [], NOW)),
    "set_stated": (None, lambda s, _: s.set_stated("u1", "hebrew_fluency", "fluent", NOW)),
    "release": (_stated, lambda s, _: s.release("u1", "hebrew_fluency", NOW)),
    "delete_slot": (_slot, lambda s, _: s.delete_slot("u1", "answer_style")),
    "delete_slot_if_inferred": (
        _slot,
        lambda s, _: s.delete_slot_if_inferred("u1", "answer_style"),
    ),
    "clear": (None, lambda s, _: s.clear("u1")),
    "add_observation": (
        None,
        lambda s, _: s.add_observation(Observation("u1", "s1", "m1", "hi", NOW)),
    ),
    "mark_consumed": (
        _observed,
        lambda s, _: s.mark_consumed("u1", [o.id for o in s.observations("u1")], NOW),
    ),
    "add_proposal": (None, lambda s, _: s.add_proposal("u1", "answer_style", "x", "q", NOW)),
    "resolve_proposal_accept": (_proposed, lambda s, p: s.resolve_proposal("u1", p.id, True, NOW)),
    "resolve_proposal_reject": (
        _proposed,
        lambda s, p: s.resolve_proposal("u1", p.id, False, NOW),
    ),
    "set_settings": (None, lambda s, _: s.set_settings("u1", store_enabled=False)),
    "expire_user": (_expired, lambda s, _: s.expire_user("u1", NOW)),
    "move": (_slot, lambda s, _: s.move("u1", "u9")),
}


@pytest.mark.parametrize("name", sorted(WRITES))
def test_every_applied_write_bumps_revision_by_one(store, name):
    setup, write = WRITES[name]
    made = setup(store) if setup else None
    before, other = store.revision("u1"), store.revision("u9")
    result = write(store, made)
    assert isinstance(result, WriteResult) and result.applied and result.reason is None
    assert store.revision("u1") == before + 1 == result.revision
    assert store.revision("u9") == other + (1 if name == "move" else 0)


# -- a strategy's own writes do not make its later writes stale ---------------------------------


def test_strategy_own_observation_does_not_stale_its_extraction(store, registry):
    strategy, _, _ = make_strategy("hybrid", store, registry)
    result = strategy.on_turn(make_turn("What is a sugya?"), NOW)
    assert len(store.observations("u1")) == 1
    assert result.updated_slots == ["preferred_language"]
    assert result.writes_applied == 1 and result.writes_refused == {}
    assert "stale_revision" not in result.diagnostics


# -- lock order and re-entrancy ----------------------------------------------------------------


def test_move_locks_in_sorted_order_no_deadlock(tstore):
    for i in range(20):
        a, b = f"a{i}", f"b{i}"
        tstore.set_inferred(a, "answer_style", "A.", [evidence()], NOW)
        tstore.set_inferred(b, "learning_habits", "B.", [evidence()], NOW)
        run_threads(lambda: tstore.move(a, b), lambda: tstore.move(b, a))
        merged = {**tstore.get_profile(a), **tstore.get_profile(b)}
        assert set(merged) == {"answer_style", "learning_habits"}


def test_accept_proposal_bumps_once_and_does_not_self_deadlock():
    store = InMemoryProfileStore()
    proposal = store.add_proposal("u1", "answer_style", "Be brief.", "q", NOW).proposal
    before = store.revision("u1")
    run_threads(lambda: store.resolve_proposal("u1", proposal.id, True, NOW), timeout=2.0)
    assert store.revision("u1") == before + 1
    assert store.get_profile("u1")["answer_style"].status == "stated"


# -- WriteResult is not a boolean ---------------------------------------------------------------


def test_write_result_has_no_truth_value(store):
    result = store.set_inferred("u1", "hebrew_fluency", "basic", [evidence()], NOW)
    for value in (result, WriteResult(True, None, 1)):
        with pytest.raises(TypeError):
            bool(value)
        with pytest.raises(TypeError):
            if not value:
                pass


# -- per-turn accounting ------------------------------------------------------------------------


def test_refused_write_is_not_counted_as_applied(store, registry):
    store.set_stated("u1", "preferred_language", "en", day(-1))
    store.set_stated("u1", "hebrew_fluency", "fluent", day(-1))
    reply = json.dumps(
        {"updates": [{"slot": "hebrew_fluency", "value": "basic", "quote": "I read Hebrew"}]}
    )
    strategy, _, _ = make_strategy("realtime", store, registry, lambda s, u: reply)
    result = strategy.on_turn(make_turn("I read Hebrew slowly"), NOW)
    assert result.writes_applied == 0 and result.updated_slots == []
    assert result.writes_refused == {"stated_wins": 1}
    assert store.get_profile("u1")["hebrew_fluency"].value == "fluent"


@pytest.mark.parametrize("mode", MODES)
def test_retried_message_is_processed_once(store, registry, mode):
    reply = json.dumps({"updates": []})
    strategy, llm, jev = strategy_for(mode, store, registry, lambda s, u: reply)
    turn = make_turn("Explain Berakhot 2a", at=day(-1))
    strategy.on_turn(turn, day(-1))
    calls = (len(llm.calls), len(jev.calls) if jev else 0)
    rows, revision = len(store.observations("u1")), store.revision("u1")
    assert rows == 1
    again = strategy.on_turn(turn, day(-1))
    assert again.skipped_reason == "duplicate"
    assert (len(llm.calls), len(jev.calls) if jev else 0) == calls
    assert len(store.observations("u1")) == rows and store.revision("u1") == revision


def test_concurrent_turns_report_their_own_tokens(registry):
    store = InMemoryProfileStore()
    barrier = threading.Barrier(2, timeout=5)

    def respond(system, user):
        barrier.wait()  # both turns are inside their model call at the same time
        return "{}"

    strategy, llm, _ = make_strategy("realtime", store, registry, respond)
    texts = {"alice": "Explain Berakhot", "bob": "Explain the first Mishnah of Pirkei Avot " * 5}
    results: dict[str, object] = {}

    def turn(user):
        results[user] = strategy.on_turn(make_turn(texts[user], user=user), NOW)

    run_threads(lambda: turn("alice"), lambda: turn("bob"))
    for user, text in texts.items():
        (call,) = [c for c in llm.calls if text.strip()[:40] in c["user"]]
        expected = (len(call["system"]) + len(call["user"])) // 4
        assert results[user].llm_calls == 1
        assert results[user].input_tokens == expected
    assert results["alice"].input_tokens != results["bob"].input_tokens
