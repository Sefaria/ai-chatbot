"""The lab testing switches are on for a user with no saved choice; a saved choice wins. Made-up text only."""

import pytest

from chat.V2.personalization import runtime

from chat.V2.personalization import profile_store as store

pytestmark = pytest.mark.django_db


def test_new_user_without_a_row_gets_both_switches_on():
    s = store.get_settings("new-user")
    assert s.show_plain_response is True
    assert s.update_every_message is True


def test_saved_choice_of_off_is_kept():
    store.set_testing("u1", update_every_message=False, show_plain_response=False)
    s = store.get_settings("u1")
    assert s.show_plain_response is False
    assert s.update_every_message is False


def test_row_created_by_saving_instructions_gets_both_switches_on():
    store.set_instructions("u1", "be brief")
    s = store.get_settings("u1")
    assert s.show_plain_response is True
    assert s.update_every_message is True


def test_every_message_switch_does_nothing_while_live_triggers_are_off(settings):
    settings.PERSONALIZATION_LIVE_TRIGGERS = False
    assert runtime.update_every_message("new-user") is False
    settings.PERSONALIZATION_LIVE_TRIGGERS = True
    assert runtime.update_every_message("new-user") is True


def test_live_triggers_default_to_off():
    from django.conf import settings as django_settings

    assert django_settings.PERSONALIZATION_LIVE_TRIGGERS is False
