from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.service import PersonalizationService
from chat.V2.personalization.strategies.base import build_strategy

from .conftest import NOW, make_strategy, make_turn


def test_explicit_makes_no_model_calls_and_stores_nothing(store, registry):
    strategy, llm, ledger = make_strategy("explicit", store, registry)
    result = strategy.on_turn(make_turn("I read Hebrew fluently. From now on be brief."), NOW)
    batch = strategy.run_batch(NOW)
    assert llm.calls == [] and ledger.entries == []
    assert result.llm_calls == 0 and result.updated_slots == [] and batch.llm_calls == 0
    assert store.get_profile("u1") == {} and store.observations("u1") == []


def test_interview_writes_validated_stated_slots(store, registry):
    strategy, llm, _ = make_strategy("explicit", store, registry)
    answers = {
        "hebrew_fluency": "intermediate",
        "text_categories": ["Talmud", "Pizza", "Musar"],
        "answer_style": " Short   answers ",
        "terminology_comfort": "expert",
        "nope": "x",
    }
    written = strategy.apply_interview("u1", answers, NOW)
    assert sorted(written) == ["answer_style", "hebrew_fluency", "text_categories"]
    profile = store.get_profile("u1")
    assert all(s.status == "stated" for s in profile.values())
    assert profile["text_categories"].value == ["Talmud", "Musar"]
    assert profile["answer_style"].value == "Short answers"
    assert llm.calls == []
    assert not store.set_inferred("u1", "hebrew_fluency", "none", [], NOW)


def test_interview_respects_store_toggle(store, registry):
    store.set_settings("u1", store_enabled=False)
    strategy, _, _ = make_strategy("explicit", store, registry)
    assert strategy.apply_interview("u1", {"hebrew_fluency": "basic"}, NOW) == []
    assert store.get_profile("u1") == {}


def test_build_strategy_names(store, registry):
    for name in ("realtime", "nightly", "hybrid", "explicit"):
        assert make_strategy(name, store, registry)[0].name == name
    with pytest.raises(ValueError):
        build_strategy("bogus", store, registry, FakeLLMClient(lambda s, u: ""), None, "m")


def test_service_is_empty_when_off_anonymous_or_opted_out(store, registry):
    store.set_stated("u1", "answer_style", "Be brief.", NOW)
    strategy, _, _ = make_strategy("nightly", store, registry)
    on = PersonalizationService(store, registry, strategy, mode="nightly")
    assert on.profile_context("u1", NOW) == (
        "- Preferred answer style: Be brief. (set by the learner)",
        ["answer_style"],
    )
    assert on.profile_context(None, NOW) == ("", [])
    assert on.profile_context("other", NOW) == ("", [])
    assert PersonalizationService(store, registry, strategy, mode="off").profile_context(
        "u1", NOW
    ) == ("", [])
    store.set_settings("u1", personalize_enabled=False)
    assert on.profile_context("u1", NOW) == ("", [])


def test_service_after_turn_routes_to_the_strategy_only_when_on(store, registry):
    strategy, _, _ = make_strategy("nightly", store, registry)
    off = PersonalizationService(store, registry, strategy, mode="off")
    assert off.after_turn(make_turn("hello"), NOW) is None and store.observations("u1") == []
    on = PersonalizationService(store, registry, strategy, mode="nightly")
    assert on.after_turn(make_turn("hello"), NOW).skipped_reason is None
    assert len(store.observations("u1")) == 1


def test_service_mode_defaults_to_the_setting(store, registry):
    assert PersonalizationService(store, registry, None).mode == "off"
    assert PersonalizationService(store, registry, None).after_turn(make_turn("x"), NOW) is None


def test_batch_command_is_a_noop_when_off(settings):
    settings.PERSONALIZATION_MODE = "off"
    out = StringIO()
    call_command("personalization_batch", stdout=out)
    assert "off" in out.getvalue()


@pytest.mark.django_db
def test_batch_command_runs_the_configured_strategy(settings, registry):
    from chat.V2.personalization.store import DjangoProfileStore

    settings.PERSONALIZATION_MODE = "nightly"
    store = DjangoProfileStore()
    strategy, _, _ = make_strategy("nightly", store, registry)
    strategy.on_turn(make_turn("Explain Berakhot 2a"), NOW)
    fake = FakeLLMClient(lambda s, u: '{"updates": [], "clear": []}')
    with patch(
        "chat.management.commands.personalization_batch.AnthropicLLMClient", return_value=fake
    ):
        out = StringIO()
        call_command("personalization_batch", stdout=out)
    assert "nightly: 1 users" in out.getvalue() and len(fake.calls) == 1
    assert store.users_with_pending() == []
