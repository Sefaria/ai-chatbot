"""The prompt chapters load from disk and have a stable hash."""

import pytest

from chat.V2.prompts.chapters import chapter_sha, load_chapter

NAMES = ["personalization_answering", "profile_writer"]


@pytest.mark.parametrize("name", NAMES)
def test_chapter_loads_with_its_rules(name):
    text = load_chapter(name)
    if name == "personalization_answering":
        assert "Instructions override the Profile" in text
    else:
        assert "under 150 words" in text
        assert "link clicks" not in text


def test_both_chapters_carry_the_core_rules():
    assert "Instructions override the Profile" in load_chapter("personalization_answering")
    assert "under 150 words" in load_chapter("profile_writer")


@pytest.mark.parametrize("name", NAMES)
def test_chapter_sha_is_stable(name):
    first = chapter_sha(name)
    assert first == chapter_sha(name) and len(first) == 12
    int(first, 16)


def test_shas_differ_and_unknown_names_fail():
    assert chapter_sha(NAMES[0]) != chapter_sha(NAMES[1])
    with pytest.raises(FileNotFoundError):
        load_chapter("no_such_chapter")
    with pytest.raises(ValueError):
        load_chapter("../models")
