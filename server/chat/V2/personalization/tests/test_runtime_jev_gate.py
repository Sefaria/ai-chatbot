"""The Jev client is built only when PERSONALIZATION_ALLOW_JEV is exactly "true"."""

from unittest.mock import patch

import pytest

from chat.V2.personalization import runtime


@pytest.fixture(autouse=True)
def fresh_services():
    runtime._services.clear()
    yield
    runtime._services.clear()


def test_jev_client_not_built_without_allow_flag(settings):
    settings.PERSONALIZATION_ALLOW_JEV = ""
    with patch("chat.V2.personalization.jev.HttpJevClient") as client:
        runtime.get_service("hybrid_jev")
    client.assert_not_called()


@pytest.mark.parametrize("value", ["", "1", "True", "TRUE", "yes", " true"])
def test_only_the_exact_string_true_allows_jev(settings, value):
    settings.PERSONALIZATION_ALLOW_JEV = value
    with patch("chat.V2.personalization.jev.HttpJevClient") as client:
        service = runtime.get_service("hybrid_jev")
    client.assert_not_called()
    assert service.strategy.name == "hybrid"


def test_hybrid_jev_mode_served_as_hybrid_without_flag(settings):
    settings.PERSONALIZATION_ALLOW_JEV = ""
    with patch.object(runtime, "logger") as log:
        service = runtime.get_service("hybrid_jev")
        assert service is not None and service.strategy.name == "hybrid"
        log.warning.assert_called_once()
        # Cached per mode: a second request does not log again.
        assert runtime.get_service("hybrid_jev") is service
        log.warning.assert_called_once()


def test_jev_client_built_with_allow_flag(settings):
    settings.PERSONALIZATION_ALLOW_JEV = "true"
    with patch("chat.V2.personalization.jev.HttpJevClient") as client:
        service = runtime.get_service("hybrid_jev")
    client.assert_called_once_with()
    assert service.strategy.name == "hybrid_jev"


def test_other_modes_never_build_a_jev_client(settings):
    settings.PERSONALIZATION_ALLOW_JEV = "true"
    with patch("chat.V2.personalization.jev.HttpJevClient") as client:
        assert runtime.get_service("hybrid").strategy.name == "hybrid"
    client.assert_not_called()
