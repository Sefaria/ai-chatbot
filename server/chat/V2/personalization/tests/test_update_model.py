"""Data model for the nightly update: the `user` origin, `last_run_at`, the Instructions date."""

import json
from datetime import UTC, datetime

import pytest

from chat.models import PersonalizationSettings, ProfileSection
from chat.V2.personalization import profile_store as store
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.profile_edit import edit_profile
from chat.V2.personalization.profile_store import SectionData
from chat.V2.prompts.chapters import load_chapter

pytestmark = pytest.mark.django_db

USER = "update-model-user"
T = datetime(2026, 11, 1, tzinfo=UTC)
EDIT = load_chapter("profile_alter")


def _alter(*sections):
    reply = json.dumps({"status": "applied", "reason": "ok", "sections": list(sections)})

    def respond(system, user):
        return reply if system == EDIT else "no"

    return edit_profile(USER, "change it", llm=FakeLLMClient(respond))[0]


def _put(*rows):
    store.replace_sections(
        USER,
        store.get_settings(USER).profile_revision,
        list(rows),
        updated_by="la",
        processed_through=None,
        set_notice=False,
    )


def _interests(body="You study many books."):
    return SectionData("current_interests", "core", "Current interests", body, "inferred", T, 0)


def test_user_origin_round_trips():
    _put(SectionData("current_interests", "core", "Current interests", "x", "user", T, 0))
    assert store.get_sections(USER)[0].origin == "user"
    assert ProfileSection.Origin.USER == "user"


def test_last_run_at_exists_and_defaults_to_none():
    row = PersonalizationSettings.objects.create(user_id=USER)
    assert row.last_run_at is None


def test_toggling_instructions_sets_the_date_and_repeating_does_not():
    assert store.set_enabled(USER, "instructions", False).instructions_updated_at is not None
    first = store.get_settings(USER).instructions_updated_at
    assert store.set_enabled(USER, "instructions", False).instructions_updated_at == first
    assert store.set_enabled(USER, "instructions", True).instructions_updated_at > first


def test_toggling_the_profile_leaves_the_instructions_date_alone():
    store.set_enabled(USER, "profile", False)
    assert store.get_settings(USER).instructions_updated_at is None


def test_text_change_sets_the_date_and_same_text_does_not():
    store.set_instructions(USER, "Be brief.")
    first = store.get_settings(USER).instructions_updated_at
    assert first is not None
    store.set_instructions(USER, "Be brief.")
    assert store.get_settings(USER).instructions_updated_at == first


def test_alter_add_and_change_save_origin_user_with_a_date():
    _put(_interests())
    assert (
        _alter(
            {"key": "current_interests", "heading": "x", "body": "You study parsha."},
            {"key": None, "heading": "Teaching", "body": "You teach on Fridays."},
        )
        == "applied"
    )
    by_heading = {s.heading: s for s in store.get_sections(USER)}
    assert by_heading["Current interests"].body == "You study parsha."
    for s in by_heading.values():
        assert s.origin == "user" and s.last_supported_at > T


def test_alter_removal_deletes_the_section():
    _put(_interests())
    assert _alter({"key": "current_interests", "heading": "", "body": ""}) == "applied"
    assert store.get_sections(USER) == []


def test_clear_sets_cleared_at_and_removes_sections():
    _put(_interests())
    assert store.clear_profile(USER).cleared_at is not None
    assert store.get_sections(USER) == []
