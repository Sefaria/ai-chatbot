"""Prompt chapters: Braintrust by slug when set, bundled file otherwise, source always reportable."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from chat.V2.personalization import runtime
from chat.V2.personalization.tests.test_injection import lab_on, seed  # noqa: F401
from chat.V2.prompts import chapters

NAME = "profile_writer"
GET_SERVICE = "chat.V2.prompts.prompt_service.get_prompt_service"


@pytest.fixture(autouse=True)
def _fresh_failures():
    chapters._failed_at.clear()
    yield
    chapters._failed_at.clear()


def fake_service(text="remote text", version="v7"):
    service = MagicMock()
    service.get_core_prompt.return_value = SimpleNamespace(
        text=text, version=version, prompt_id="slug-x"
    )
    return service


def test_slug_unset_is_bundled():
    with override_settings(PERSONALIZATION_PROMPT_SLUG_PROFILE_WRITER=""):
        assert chapters.load_chapter(NAME) == chapters._bundled(NAME)
        assert chapters.chapter_source(NAME) == f"bundled@{chapters.chapter_sha(NAME)}"


def test_slug_set_uses_service():
    service = fake_service()
    with override_settings(PERSONALIZATION_PROMPT_SLUG_PROFILE_WRITER="writer-1"):
        with patch(GET_SERVICE, return_value=service):
            assert chapters.load_chapter(NAME) == "remote text"
            assert chapters.chapter_source(NAME) == "braintrust:writer-1@v7"
    assert service.get_core_prompt.call_args.kwargs["prompt_id"] == "writer-1"


def test_fetch_error_falls_back_warns_once_and_backs_off():
    service = MagicMock()
    service.get_core_prompt.side_effect = RuntimeError("boom")
    with override_settings(PERSONALIZATION_PROMPT_SLUG_PROFILE_WRITER="writer-1"):
        with patch(GET_SERVICE, return_value=service):
            with patch.object(chapters.logger, "warning") as warn:
                assert chapters.load_chapter(NAME) == chapters._bundled(NAME)
                assert chapters.load_chapter(NAME) == chapters._bundled(NAME)
                assert chapters.chapter_source(NAME).startswith("bundled@")
    assert service.get_core_prompt.call_count == 1
    assert warn.call_count == 1


def test_backoff_expires_after_60_seconds():
    service = MagicMock()
    service.get_core_prompt.side_effect = RuntimeError("boom")
    with override_settings(PERSONALIZATION_PROMPT_SLUG_PROFILE_WRITER="writer-1"):
        with patch(GET_SERVICE, return_value=service):
            with patch.object(chapters.time, "monotonic", return_value=1000.0):
                chapters.load_chapter(NAME)
            with patch.object(chapters.time, "monotonic", return_value=1061.0):
                chapters.load_chapter(NAME)
    assert service.get_core_prompt.call_count == 2


def test_service_construction_error_falls_back():
    with override_settings(PERSONALIZATION_PROMPT_SLUG_PROFILE_WRITER="writer-1"):
        with patch(GET_SERVICE, side_effect=RuntimeError("no key")):
            assert chapters.load_chapter(NAME) == chapters._bundled(NAME)
            assert chapters.chapter_source(NAME) == f"bundled@{chapters.chapter_sha(NAME)}"


@pytest.mark.django_db
def test_personalized_turn_meta_has_prompt_versions(lab_on):  # noqa: F811
    seed("u1")
    meta = runtime.context_for_request("u1", plain=False).meta
    assert meta["prompt_versions"]["personalization_answering"] == chapters.chapter_source(
        "personalization_answering"
    )
    assert meta["chapter_sha"] == chapters.chapter_sha("personalization_answering")


SLUG_VARS = {
    "PERSONALIZATION_PROMPT_SLUG_ANSWERING": "la-p13n-personalization-answering",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_USED": "la-p13n-personalization-profile-used",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_WRITER": "la-p13n-profile-writer",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_EXTRACTION": "la-p13n-profile-extraction",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_DREAM": "la-p13n-profile-dream",
    "PERSONALIZATION_PROMPT_SLUG_PROFILE_EDIT": "la-p13n-profile-edit",
}


def _production_slug_defaults(monkeypatch):
    """Evaluate the slug assignments of settings.py alone, ignoring test settings."""
    import ast
    from pathlib import Path

    from chatbot_server import settings as production

    tree = ast.parse(Path(production.__file__).read_text())
    lines = [
        ast.get_source_segment(Path(production.__file__).read_text(), node)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", "") in SLUG_VARS
    ]
    namespace = {"os": __import__("os")}
    exec("\n".join(lines), namespace)  # noqa: S102 - our own settings source
    return {name: namespace[name] for name in SLUG_VARS}


def test_settings_default_to_live_braintrust_slugs(monkeypatch):
    for name in SLUG_VARS:
        monkeypatch.delenv(name, raising=False)
    assert _production_slug_defaults(monkeypatch) == SLUG_VARS


def test_settings_slug_env_override_wins(monkeypatch):
    for name in SLUG_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PERSONALIZATION_PROMPT_SLUG_PROFILE_DREAM", "custom-slug")
    slugs = _production_slug_defaults(monkeypatch)
    assert slugs["PERSONALIZATION_PROMPT_SLUG_PROFILE_DREAM"] == "custom-slug"
    assert slugs["PERSONALIZATION_PROMPT_SLUG_ANSWERING"] == SLUG_VARS["PERSONALIZATION_PROMPT_SLUG_ANSWERING"]
