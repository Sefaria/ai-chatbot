"""The lab testing switches are on for a user with no saved choice; a saved choice wins. Made-up text only."""

import pytest

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
