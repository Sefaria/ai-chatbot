"""One suite, run against the in-memory and the Django store."""

from chat.V2.personalization.types import Observation

from .conftest import NOW, day, evidence


def obs(user, session="s1", message="m1", text="hello", at=NOW):
    return Observation(user, session, message, text, at, {"language": "en"})


def test_set_and_get_inferred(store):
    assert store.set_inferred("a", "hebrew_fluency", "basic", [evidence()], NOW).applied
    slot = store.get_profile("a")["hebrew_fluency"]
    assert (slot.value, slot.status, slot.evidence[0].quote) == ("basic", "inferred", "q")
    assert slot.updated_at == NOW


def test_list_values_round_trip(store):
    store.set_inferred("a", "text_categories", ["Talmud", "Musar"], [evidence()], NOW)
    assert store.get_profile("a")["text_categories"].value == ["Talmud", "Musar"]


def test_users_are_isolated(store):
    store.set_inferred("a", "hebrew_fluency", "basic", [evidence()], NOW)
    store.add_observation(obs("a"))
    store.add_proposal("a", "answer_style", "Be brief.", "q", NOW)
    assert store.get_profile("b") == {}
    assert store.observations("b") == []
    assert store.proposals("b") == []
    assert store.get_settings("b").store_enabled


def test_proposal_of_one_user_cannot_be_resolved_by_another(store):
    proposal = store.add_proposal("a", "answer_style", "Be brief.", "q", NOW).proposal
    assert not store.resolve_proposal("b", proposal.id, True, NOW).applied
    assert store.get_profile("b") == {}
    assert store.proposals("a", "pending")


def test_stated_lock_refuses_inferred_writes(store):
    store.set_stated("a", "hebrew_fluency", "fluent", NOW)
    refused = store.set_inferred("a", "hebrew_fluency", "none", [evidence()], day(1))
    assert (refused.applied, refused.reason) == (False, "stated_wins")
    slot = store.get_profile("a")["hebrew_fluency"]
    assert (slot.value, slot.status) == ("fluent", "stated")


def test_release_returns_slot_to_inferred_and_keeps_value(store):
    store.set_stated("a", "hebrew_fluency", "fluent", NOW)
    store.release("a", "hebrew_fluency", day(1))
    assert store.get_profile("a")["hebrew_fluency"].status == "inferred"
    assert store.get_profile("a")["hebrew_fluency"].value == "fluent"
    assert store.set_inferred("a", "hebrew_fluency", "basic", [evidence()], day(2)).applied


def test_evidence_is_capped_at_three(store):
    many = [evidence(quote=f"q{i}", message=f"m{i}") for i in range(5)]
    store.set_inferred("a", "answer_style", "Be brief.", many, NOW)
    assert [e.quote for e in store.get_profile("a")["answer_style"].evidence] == ["q2", "q3", "q4"]


def test_delete_slot(store):
    store.set_inferred("a", "hebrew_fluency", "basic", [evidence()], NOW)
    store.set_inferred("a", "answer_style", "Be brief.", [evidence()], NOW)
    store.delete_slot("a", "hebrew_fluency")
    assert list(store.get_profile("a")) == ["answer_style"]


def test_clear_removes_slots_observations_and_proposals_for_one_user(store):
    for user in ("a", "b"):
        store.set_inferred(user, "hebrew_fluency", "basic", [evidence()], NOW)
        store.add_observation(obs(user))
        store.add_proposal(user, "answer_style", "Be brief.", "q", NOW)
    store.clear("a")
    assert store.get_profile("a") == {} and store.observations("a") == []
    assert store.proposals("a") == []
    assert "hebrew_fluency" in store.get_profile("b")
    assert len(store.observations("b")) == 1 and len(store.proposals("b")) == 1


def test_observations_since_and_consumed(store):
    store.add_observation(obs("a", message="m1", at=day(-40)))
    store.add_observation(obs("a", message="m2", at=day(-1)))
    assert [o.message_id for o in store.observations("a", since=day(-30))] == ["m2"]
    assert [o.message_id for o in store.observations("a")] == ["m1", "m2"]
    assert store.users_with_pending() == ["a"]
    store.mark_consumed("a", [o.id for o in store.observations("a")], NOW)
    assert store.users_with_pending() == []
    assert all(o.consumed_at == NOW for o in store.observations("a"))


def test_users_with_pending_lists_only_users_with_unconsumed(store):
    store.add_observation(obs("a"))
    store.add_observation(obs("b"))
    store.mark_consumed("b", [o.id for o in store.observations("b")], NOW)
    assert store.users_with_pending() == ["a"]


def test_proposal_accept_writes_stated_slot(store):
    proposal = store.add_proposal("a", "answer_style", "Be brief.", "keep it short", NOW).proposal
    assert proposal.status == "pending"
    resolved = store.resolve_proposal("a", proposal.id, True, day(1)).proposal
    assert resolved.status == "accepted"
    slot = store.get_profile("a")["answer_style"]
    assert (slot.value, slot.status) == ("Be brief.", "stated")
    assert store.proposals("a", "pending") == []
    assert len(store.proposals("a", "accepted")) == 1


def test_proposal_reject_writes_nothing(store):
    proposal = store.add_proposal("a", "answer_style", "Be brief.", "q", NOW).proposal
    assert store.resolve_proposal("a", proposal.id, False, NOW).proposal.status == "rejected"
    assert store.get_profile("a") == {}
    again = store.resolve_proposal("a", proposal.id, True, NOW)
    assert (again.applied, again.proposal) == (False, None)


def test_move_merges_with_stated_target_winning_then_newer(store):
    store.set_inferred("anon", "hebrew_fluency", "basic", [evidence()], day(-1))
    store.set_inferred("anon", "answer_style", "Be brief.", [evidence()], day(-1))
    store.set_inferred("anon", "terminology_comfort", "some", [evidence()], day(-5))
    store.set_inferred("anon", "learning_habits", "Daf Yomi.", [evidence()], day(-1))
    store.set_stated("user", "hebrew_fluency", "fluent", day(-10))
    store.set_inferred("user", "answer_style", "Be long.", [evidence()], day(-3))
    store.set_inferred("user", "terminology_comfort", "comfortable", [evidence()], day(-2))
    store.add_observation(obs("anon"))
    store.add_proposal("anon", "answer_style", "Be brief.", "q", NOW)
    store.move("anon", "user")
    profile = store.get_profile("user")
    assert profile["hebrew_fluency"].value == "fluent"
    assert profile["answer_style"].value == "Be brief."
    assert profile["terminology_comfort"].value == "comfortable"
    assert profile["learning_habits"].value == "Daf Yomi."
    assert store.get_profile("anon") == {}
    assert len(store.observations("user")) == 1 and store.observations("anon") == []
    assert len(store.proposals("user")) == 1 and store.proposals("anon") == []


def test_move_keeps_the_stricter_settings(store):
    store.set_settings("anon", store_enabled=False)
    store.move("anon", "user")
    assert store.get_settings("user").store_enabled is False
    assert store.get_settings("anon").store_enabled is True


def test_settings_toggles_are_independent(store):
    assert store.get_settings("a").personalize_enabled and store.get_settings("a").store_enabled
    store.set_settings("a", personalize_enabled=False)
    settings = store.get_settings("a")
    assert (settings.personalize_enabled, settings.store_enabled) == (False, True)
    store.set_settings("a", store_enabled=False)
    settings = store.get_settings("a")
    assert (settings.personalize_enabled, settings.store_enabled) == (False, False)
    assert store.get_settings("b").personalize_enabled


def test_expire_deletes_only_expired_inferred_slots(store):
    store.set_inferred("a", "text_categories", ["Talmud"], [evidence()], NOW, expires_at=day(60))
    store.set_inferred("a", "answer_style", "Be brief.", [evidence()], NOW)
    store.set_stated("a", "hebrew_fluency", "fluent", NOW)
    assert store.expire(day(30)) == 0
    assert store.expire(day(61)) == 1
    assert set(store.get_profile("a")) == {"answer_style", "hebrew_fluency"}


def test_expire_never_touches_stated_slots(store):
    store.set_inferred("a", "text_categories", ["Talmud"], [evidence()], NOW, expires_at=day(1))
    store.set_stated("a", "text_categories", ["Musar"], day(0))
    assert store.expire(day(100)) == 0
    assert store.get_profile("a")["text_categories"].value == ["Musar"]
