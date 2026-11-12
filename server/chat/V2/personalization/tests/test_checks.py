"""Code checks on a Profile the writer produced. Made-up text only."""

from datetime import UTC, datetime

from chat.V2.personalization.checks import MAX_DYNAMIC, MAX_WORDS, check_profile
from chat.V2.personalization.llm import FakeLLMClient
from chat.V2.personalization.profile_store import SectionData

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def sec(heading, body, *, kind="dynamic", key=None, position=0):
    return SectionData(key, kind, heading, body, "inferred", NOW, position)


def llm_says(answer):
    return FakeLLMClient(lambda system, user: answer)


def test_passes_a_small_profile_without_a_model():
    assert check_profile([sec("Reply language", "You write in English.", kind="core")], None).ok


def test_150_words_pass_and_151_fail():
    assert MAX_WORDS == 150
    assert check_profile([sec("Notes", " ".join(["word"] * 150))], None).ok
    result = check_profile([sec("Notes", " ".join(["word"] * 151))], None)
    assert not result.ok and result.failed == "words"


def test_words_count_across_sections():
    sections = [sec("A", " ".join(["w"] * 80)), sec("B", " ".join(["w"] * 71))]
    assert check_profile(sections, None).failed == "words"


def test_five_dynamic_sections_fail():
    assert MAX_DYNAMIC == 4
    four = [sec(f"Topic {i}", "x") for i in range(4)]
    assert check_profile(four, None).ok
    result = check_profile(four + [sec("Topic 5", "x")], None)
    assert result.failed == "dynamic_cap"


def test_core_sections_do_not_count_toward_the_dynamic_cap():
    core = [sec(f"Slot {i}", "x", kind="core", key=f"k{i}") for i in range(8)]
    assert check_profile(core + [sec("Teaching", "x")], None).ok


def test_duplicate_heading_after_lowercase_and_strip():
    result = check_profile([sec("Teaching", "a"), sec("  teaching ", "b")], None)
    assert result.failed == "duplicate_heading"


def test_never_store_terms():
    result = check_profile(
        [sec("Notes", "You study while caring for your mother's illness.")], None
    )
    assert result.failed == "never_store_terms"


def test_never_store_terms_in_a_heading_and_in_hebrew():
    assert check_profile([sec("Mourning", "x")], None).failed == "never_store_terms"
    assert check_profile([sec("Notes", "אחרי השבעה")], None).failed == "never_store_terms"


def test_model_check_yes_rejects():
    llm = llm_says("YES")
    result = check_profile([sec("Notes", "You read slowly.")], llm)
    assert result.failed == "never_store_model" and len(llm.calls) == 1


def test_model_check_no_passes_and_gets_the_profile_text():
    llm = llm_says("no")
    assert check_profile([sec("Notes", "You read slowly.")], llm).ok
    assert "You read slowly." in llm.calls[0]["user"]


def test_empty_section_fails():
    assert check_profile([sec("Notes", "   ")], None).failed == "empty_section"


def test_term_screen_runs_before_the_model():
    llm = llm_says("no")
    check_profile([sec("Notes", "your illness")], llm)
    assert llm.calls == []


def seq(*replies):
    it = iter(replies)
    llm = FakeLLMClient(lambda system, user: next(it))
    return llm


HEBREW = [sec("Reply language", "את כותבת בעברית ואוהבת תשובות קצרות.", kind="core")]


def test_hebrew_profile_with_no_passes_and_prompt_demands_english_token():
    llm = seq("NO")
    assert check_profile(HEBREW, llm).ok
    assert "exactly YES or NO" in llm.calls[0]["system"] and "Hebrew" in llm.calls[0]["system"]


def test_verdict_parse_normalizes_case_and_punctuation():
    for answer in ("no", " No. ", '"NO"', "NO!", "no\n"):
        assert check_profile(HEBREW, seq(answer)).ok, answer


def test_unparseable_replies_retry_once_then_pass_on_no():
    for first in ("לא", "not sure", "No, it is fine.", ""):
        llm = seq(first, "NO")
        assert check_profile(HEBREW, llm).ok, first
        assert len(llm.calls) == 2


def test_two_unparseable_replies_reject_with_a_distinct_code():
    llm = seq("לא", "not sure")
    result = check_profile(HEBREW, llm)
    assert not result.ok and result.failed == "never_store_unparseable"
    assert len(llm.calls) == 2


def test_yes_rejects_without_a_retry():
    llm = seq("YES")
    result = check_profile(HEBREW, llm)
    assert not result.ok and result.failed == "never_store_model" and len(llm.calls) == 1


def test_never_store_checks_run_before_the_size_checks():
    long_and_sensitive = [sec("Notes", "Your wife likes " + " ".join(["w"] * 200))]
    assert check_profile(long_and_sensitive, None).failed == "never_store_terms"
    five = [sec(f"Topic {i}", "x") for i in range(5)]
    assert check_profile(five, llm_says("yes")).failed == "never_store_model"
    assert check_profile(five, llm_says("maybe")).failed == "never_store_unparseable"
    assert check_profile(five, llm_says("no")).failed == "dynamic_cap"
    too_long = [sec("Notes", " ".join(["w"] * 200))]
    assert check_profile(too_long, llm_says("yes")).failed == "never_store_model"
    assert check_profile(too_long, llm_says("no")).failed == "words"
