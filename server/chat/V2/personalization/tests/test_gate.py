"""The one server-side gate: who may turn personalization on, and for whom."""

from types import SimpleNamespace

import pytest

from chat.V2.personalization import gate
from chat.V2.personalization.gate import RequestedPersonalization, resolve

USER = "user-1"


def make_settings(*, mode="off", enabled=True, ids="user-1", injection="always"):
    return SimpleNamespace(
        PERSONALIZATION_MODE=mode,
        PERSONALIZATION_INJECTION=injection,
        PERSONALIZATION_OVERRIDE_ENABLED=enabled,
        PERSONALIZATION_OVERRIDE_USER_IDS=ids,
    )


@pytest.fixture(autouse=True)
def non_production_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "preview")


def asked(**kw):
    return RequestedPersonalization(**{"mode": "realtime", **kw})


def test_setting_mode_applies_without_override():
    config = resolve(make_settings(mode="hybrid", enabled=False, ids=""), USER, asked(mode=None))
    assert (config.mode, config.source, config.override_refused) == ("hybrid", "setting", False)
    assert config.injection == "always" and config.store_user_id == USER

    off = resolve(make_settings(mode="off"), USER, RequestedPersonalization())
    assert (off.mode, off.source, off.override_refused) == ("off", "off", False)


@pytest.mark.parametrize(
    "enabled,environment,allowlisted",
    [
        (True, "preview", True),  # the only honoured row
        (True, "preview", False),
        (True, "prod", True),
        (True, "prod", False),
        (False, "preview", True),
        (False, "preview", False),
        (False, "prod", True),
        (False, "prod", False),
    ],
)
def test_override_needs_flag_environment_and_allowlist(
    monkeypatch, enabled, environment, allowlisted
):
    monkeypatch.setenv("ENVIRONMENT", environment)
    config = resolve(
        make_settings(enabled=enabled, ids="user-1" if allowlisted else "someone-else"),
        USER,
        asked(),
    )
    honoured = enabled and environment == "preview" and allowlisted
    assert (config.source == "override") is honoured
    assert config.mode == ("realtime" if honoured else "off")
    assert config.override_refused is (not honoured)


def test_override_refused_when_environment_is_unset(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    config = resolve(make_settings(), USER, asked())
    assert config.source == "off" and config.override_refused


@pytest.mark.parametrize("value", ["prod", "production", "PROD", " Prod ", "beta"])
def test_production_values_never_honour_an_override(monkeypatch, value):
    # Prod ai-chatbot sets ENVIRONMENT=prod and beta sets beta (wiki: runbooks/
    # verify-first-party-user-agent-locally.md, from AppliedAI-Infrastructure#170).
    monkeypatch.setenv("ENVIRONMENT", value)
    config = resolve(make_settings(), USER, asked())
    assert config.source == "off" and config.override_refused


def test_isstaff_and_labs_do_not_open_the_override(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "preview")
    requested = RequestedPersonalization.from_context(
        {"isStaff": True, "labs": True, "personalizationMode": "realtime"}
    )
    config = resolve(make_settings(enabled=False, ids=""), USER, requested)
    assert config.source == "off" and config.override_refused
    # Staff and labs alone ask for nothing and are not a refusal.
    quiet = RequestedPersonalization.from_context({"isStaff": True, "labs": True})
    config = resolve(make_settings(), USER, quiet)
    assert config.source == "off" and not config.override_refused


def test_refused_override_is_reported():
    config = resolve(make_settings(enabled=False), USER, asked())
    assert config.override_refused and config.source == "off"
    assert config.fixture_profile is None and config.store_user_id == USER
    # Nothing asked: nothing to refuse.
    assert not resolve(
        make_settings(enabled=False), USER, RequestedPersonalization()
    ).override_refused
    # A refused request still gets the server's own mode, not the requested one.
    config = resolve(make_settings(mode="nightly", enabled=False), USER, asked())
    assert (config.mode, config.source, config.override_refused) == ("nightly", "setting", True)


def test_subject_suffix_changes_store_user_only():
    plain = resolve(make_settings(), USER, asked())
    subject = resolve(make_settings(), USER, asked(subject="learner-a"))
    assert plain.store_user_id == USER
    assert subject.store_user_id == "user-1:eval:learner-a"
    assert (subject.mode, subject.source) == (plain.mode, plain.source)
    # Without an honoured override the subject is ignored.
    refused = resolve(make_settings(enabled=False), USER, asked(subject="learner-a"))
    assert refused.store_user_id == USER


@pytest.mark.parametrize(
    "user,subject",
    [
        ("user-1", "a" * 41),
        ("user-1", "Bad Subject"),
        ("u" * 90, "learner-a"),  # the suffix would not fit the 100-character column
    ],
)
def test_overlong_subject_is_refused(user, subject):
    config = resolve(make_settings(ids=user), user, asked(subject=subject))
    assert config.source == "off" and config.override_refused
    assert config.store_user_id == user


def test_invalid_fixture_slot_is_dropped_by_registry():
    fixture = {
        "preferred_language": "klingon",
        "no_such_slot": "x",
        "hebrew_fluency": "basic",
    }
    config = resolve(make_settings(), USER, asked(profile=fixture))
    assert config.source == "override"
    assert config.fixture_profile == {"hebrew_fluency": "basic"}


def test_gate_is_the_only_reader_of_the_override_settings():
    # Documented contract: the pinned set of production values.
    assert {"prod", "production", "beta"} <= gate.PRODUCTION_ENVIRONMENTS
