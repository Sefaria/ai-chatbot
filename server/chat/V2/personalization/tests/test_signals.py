import pytest

from chat.V2.personalization.signals import (
    cited_categories,
    detect_language,
    find_standing_instruction,
    hebrew_ratio,
    is_sensitive,
    profile_language,
    session_language,
)


def test_hebrew_ratio():
    assert hebrew_ratio("hello") == 0.0
    assert hebrew_ratio("שלום") == 1.0
    assert hebrew_ratio("12345 ...") == 0.0
    assert hebrew_ratio("שלום hello") == pytest.approx(4 / 9)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("What does Rashi say on Genesis 1:1?", "en"),
        ("מה רש״י אומר על בראשית א׳ א׳?", "he"),
        ("מה זה sugya בגמרא", "mixed"),
        ("explain the word דעת please, and also what it means in Berakhot", "en"),
        ("What is בראשית?", "mixed"),
        ("12345 !!!", None),
        ("", None),
    ],
)
def test_detect_language(text, expected):
    assert detect_language(text) == expected


def test_session_language_is_majority_and_tie_is_mixed():
    assert session_language(["hello there", "what is a sugya", "שלום עולם"]) == "en"
    assert session_language(["שלום עולם", "מה זה סוגיה", "hello"]) == "he"
    assert session_language(["hello", "שלום"]) == "mixed"
    assert session_language(["123", ""]) is None


def test_profile_language_rules():
    assert profile_language([]) is None
    assert profile_language([None]) is None
    assert profile_language(["he"]) == "he"
    assert profile_language(["en", "he", "he"]) == "he"
    assert profile_language(["he", "en", "en"]) == "en"
    assert profile_language(["en", "he"]) == "mixed"
    assert profile_language(["en", "en", "he"]) == "mixed"
    assert profile_language(["he", None, "he"]) == "he"


@pytest.mark.parametrize(
    "text",
    [
        "My mother passed away last week, what is the order of shiva?",
        "I am sitting shiva for my father",
        "I was diagnosed with cancer and want to know about healing prayers",
        "I am considering conversion to Judaism, what is giyur?",
        "Is a mamzer allowed to marry?",
        "My friend is an agunah, what can she do?",
        "אבא שלי נפטר ואני אומר קדיש על אבי",
        "ישבתי שבעה אחרי הלוויה",
        "אובחנתי עם סרטן",
        "אני בתהליך גיור",
        "האם ממזר יכול להתחתן?",
    ],
)
def test_is_sensitive_flags(text):
    assert is_sensitive(text)


@pytest.mark.parametrize(
    "text",
    [
        "What does Rashi say on Genesis 1:1?",
        "Show me the sugya about the ox that gores",
        "בראשית ברא אלהים, מה פירוש המילה ברא?",
        "Teach me the seven days of creation",
    ],
)
def test_is_sensitive_passes_ordinary_study(text):
    assert not is_sensitive(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hi. From now on, answer in Hebrew. Thanks.", "From now on, answer in Hebrew."),
        ("Please ALWAYS give me the Hebrew first", "Please ALWAYS give me the Hebrew first"),
        ("Never use transliteration.", "Never use transliteration."),
        ("Going forward keep it short", "Going forward keep it short"),
        ("מעכשיו תענה לי בעברית", "מעכשיו תענה לי בעברית"),
        ("שאלה. תמיד תציג מקורות", "תמיד תציג מקורות"),
        ("אף פעם אל תתרגם", "אף פעם אל תתרגם"),
    ],
)
def test_find_standing_instruction(text, expected):
    assert find_standing_instruction(text) == expected


def test_find_standing_instruction_none():
    assert find_standing_instruction("What does Rashi say here?") is None
    assert find_standing_instruction("Is this alwaysly true") is None


def test_cited_categories_english():
    text = "Compare Rashi on Genesis with the Zohar, and Berakhot 2a"
    assert cited_categories(text) == ["Commentary", "Tanakh", "Kabbalah", "Talmud"]
    assert cited_categories("Mesillat Yesharim and the Tanya") == ["Musar", "Chasidut"]
    assert cited_categories("Rambam's Guide for the Perplexed") == ["Jewish Thought"]
    assert cited_categories("Shulchan Arukh and the siddur") == ["Halakhah", "Liturgy"]
    assert cited_categories("Mishnah Avot") == ["Mishnah"]
    assert cited_categories("what is the weather") == []


def test_cited_categories_hebrew():
    assert cited_categories('מה רש"י אומר בבראשית?') == ["Commentary", "Tanakh"]
    assert cited_categories("תניא ומסילת ישרים") == ["Chasidut", "Musar"]
    assert cited_categories("בגמרא במסכת ברכות") == ["Talmud"]
    assert cited_categories("שולחן ערוך וסידור") == ["Halakhah", "Liturgy"]
    assert cited_categories("הזוהר") == ["Kabbalah"]
