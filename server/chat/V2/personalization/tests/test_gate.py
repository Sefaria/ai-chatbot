"""The gate: lab flags, non-production environment, signed-in users only."""

import pytest
from django.test import override_settings

from chat.V2.personalization import gate

USER = "user-1"


@pytest.fixture
def flags(monkeypatch):
    """The one honoured row: flags on, a preview environment."""
    monkeypatch.setenv("ENVIRONMENT", "preview")
    monkeypatch.setenv("PERSONALIZATION_OVERRIDE_ALLOW_ALL", "true")
    monkeypatch.setenv("PERSONALIZATION_LAB_SEED", "true")


@override_settings(PERSONALIZATION_OVERRIDE_ENABLED=True)
def test_on_for_a_signed_in_user_on_a_preview(flags):
    assert gate.enabled_for(USER) is True
    assert gate.lab_enabled() is True


@override_settings(PERSONALIZATION_OVERRIDE_ENABLED=True)
@pytest.mark.parametrize("environment", ["prod", "production", "beta", "PROD", " beta ", ""])
def test_off_in_production_and_when_unset_whatever_the_flags(flags, monkeypatch, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)
    assert gate.enabled_for(USER) is False
    assert gate.lab_enabled() is False


@override_settings(PERSONALIZATION_OVERRIDE_ENABLED=True)
def test_off_when_environment_is_missing(flags, monkeypatch):
    monkeypatch.delenv("ENVIRONMENT")
    assert gate.enabled_for(USER) is False and gate.lab_enabled() is False


@override_settings(PERSONALIZATION_OVERRIDE_ENABLED=False)
def test_off_without_the_override_flag(flags):
    assert gate.enabled_for(USER) is False


@override_settings(PERSONALIZATION_OVERRIDE_ENABLED=True)
def test_off_without_allow_all(flags, monkeypatch):
    monkeypatch.delenv("PERSONALIZATION_OVERRIDE_ALLOW_ALL")
    assert gate.enabled_for(USER) is False
    monkeypatch.setenv("PERSONALIZATION_OVERRIDE_ALLOW_ALL", "yes")
    assert gate.enabled_for(USER) is False


@override_settings(PERSONALIZATION_OVERRIDE_ENABLED=True)
@pytest.mark.parametrize("user_id", ["", "anon:1234", None])
def test_off_for_anonymous_and_empty_ids(flags, user_id):
    assert gate.enabled_for(user_id) is False


@override_settings(PERSONALIZATION_OVERRIDE_ENABLED=True)
def test_lab_needs_its_own_flag(flags, monkeypatch):
    monkeypatch.delenv("PERSONALIZATION_LAB_SEED")
    assert gate.lab_enabled() is False
    assert gate.enabled_for(USER) is True
