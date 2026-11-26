import sys
from unittest.mock import MagicMock

import pytest

import personalization

EXPECTED = {
    "personalization_answering": "la-p13n-personalization-answering",
    "personalization_profile_used": "la-p13n-personalization-profile-used",
    "profile_writer": "la-p13n-profile-writer",
    "profile_extraction": "la-p13n-profile-extraction",
    "profile_dream": "la-p13n-profile-dream",
    "profile_edit": "la-p13n-profile-edit",
}


def test_slug_table():
    assert {stem: slug for stem, slug, _, _ in personalization.PROMPTS} == EXPECTED
    for stem in EXPECTED:
        assert (personalization.CHAPTERS_DIR / f"{stem}.md").is_file()


def test_prod_env_uses_live_slugs_and_file_text():
    built = personalization.build_prompts({"BRAINTRUST_PUSH_TARGET": "prod"})
    assert [b[0] for b in built] == list(EXPECTED.values())
    for (stem, _, _, _), (_, name, _, text) in zip(personalization.PROMPTS, built):
        assert not name.startswith("[test] ")
        assert text == (personalization.CHAPTERS_DIR / f"{stem}.md").read_text(encoding="utf-8")


def test_suffix_rule():
    built = personalization.build_prompts({"PROMPT_SLUG_OVERRIDE": "me-test"})
    assert [b[0] for b in built] == [f"{s}-me-test" for s in EXPECTED.values()]
    assert all(b[1].startswith("[test] ") for b in built)


def test_gate_blocks_without_braintrust_call(monkeypatch, capsys):
    monkeypatch.delenv("PROMPT_SLUG_OVERRIDE", raising=False)
    monkeypatch.delenv("BRAINTRUST_PUSH_TARGET", raising=False)
    bt = MagicMock()
    monkeypatch.setattr(personalization, "braintrust", bt)
    with pytest.raises(SystemExit) as exc:
        personalization.push()
    assert exc.value.code == 2
    assert "ERROR: local pushes must target a sandbox slug." in capsys.readouterr().err
    assert bt.mock_calls == []


def test_push_replaces_and_publishes_once(monkeypatch):
    monkeypatch.setenv("PROMPT_SLUG_OVERRIDE", "me-test")
    monkeypatch.delenv("BRAINTRUST_PUSH_TARGET", raising=False)
    bt = MagicMock()
    monkeypatch.setattr(personalization, "braintrust", bt)
    personalization.push()
    project = bt.projects.create.return_value
    bt.projects.create.assert_called_once_with(name="On Site Agent")
    calls = project.prompts.create.call_args_list
    assert len(calls) == 6
    assert all(c.kwargs["if_exists"] == "replace" for c in calls)
    assert [c.kwargs["slug"] for c in calls] == [f"{s}-me-test" for s in EXPECTED.values()]
    assert project.add_prompt.call_count == 6
    project.publish.assert_called_once()
