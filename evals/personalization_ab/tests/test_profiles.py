import json
from datetime import UTC, datetime, timedelta

import pytest
from chat.V2.personalization import profile_store as store
from chat.V2.personalization.jev import FakeJevClient
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.profile_store import SectionData
from chat.V2.personalization.signals import is_sensitive

from personalization_ab.profiles import build_profile_a, build_profile_b
from personalization_ab.select import Turn

pytestmark = pytest.mark.django_db

USER = "ab-user"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
SENSITIVE = (
    "My father died last week and I am saying kaddish, which prayers should I read?"
)


def turns(text_for):
    return [
        Turn(USER, f"s{i % 2}", i, T0 + timedelta(minutes=i), text_for(i), "an answer")
        for i in range(10)
    ]


def llm():
    def respond(system, user):
        raise AssertionError("no model call expected")

    return FakeLLMClient(respond)


def jev():
    return FakeJevClient(lambda state, questions: {})


def test_sensitive_text_is_flagged_by_the_real_screen():
    assert is_sensitive(SENSITIVE)


def test_profile_a_all_sensitive_is_empty_with_ten_dropped():
    build = build_profile_a(USER, turns(lambda i: SENSITIVE), llm(), jev())
    assert build.text == ""
    assert len(build.dropped) == 10
    assert [n for n, _ in build.dropped] == list(range(10))
    assert all(reason == "sensitive_only" for _, reason in build.dropped)


def test_profile_b_starts_empty_even_if_the_user_had_a_profile():
    rev = store.get_settings(USER).profile_revision
    assert store.replace_sections(
        USER,
        rev,
        [
            SectionData(
                "answer_style",
                "core",
                "Kind of answers you want",
                "OLD BODY",
                "inferred",
                T0,
                0,
            )
        ],
        updated_by="la",
        processed_through=None,
        set_notice=False,
    )
    build = build_profile_b(USER, turns(lambda i: SENSITIVE), llm(), jev())
    assert "OLD BODY" not in build.text
    assert build.text == ""
    assert (
        build.dropped[0]["screened_out"] == 10 and build.dropped[0]["kept_count"] == 0
    )


def test_profile_a_success_path_with_a_fake_model():
    from chat.V2.prompts.chapters import load_chapter

    extraction = load_chapter("profile_extraction")

    def respond(system, user):
        if system == extraction:
            return json.dumps(
                {
                    "sections": [
                        {
                            "key": "answer_style",
                            "heading": "Kind of answers you want",
                            "action": "add",
                            "origin": "inferred",
                            "notes": ["likes short answers"],
                            "sessions": 2,
                        }
                    ]
                }
            )
        if "yes or no" in system:
            return "no"
        wanted = [l[3:].strip() for l in user.splitlines() if l.startswith("## ")]
        return json.dumps(
            {"sections": [{"heading": h, "body": f"Body of {h}."} for h in wanted]}
        )

    ok_jev = FakeJevClient(lambda state, qs: {})
    build = build_profile_a(
        USER,
        turns(lambda i: f"give me a short answer on topic {i}"),
        FakeLLMClient(respond),
        ok_jev,
    )
    assert build.text.strip() != ""
    assert build.revisions >= 1
