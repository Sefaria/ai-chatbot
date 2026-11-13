"""Deterministic signals computed from text: no network, no model.

The sensitive-topic filter and the book list are prototype-grade keyword lists. They are
deliberately conservative (they may drop an innocent turn) and are not a substitute for review.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Protocol

_HEBREW = re.compile(r"[֐-׿]")
_LATIN = re.compile(r"[A-Za-z]")


def hebrew_ratio(text: str) -> float:
    """Share of Hebrew letters among Hebrew and Latin letters (0.0 when there are none)."""
    hebrew = len(_HEBREW.findall(text))
    latin = len(_LATIN.findall(text))
    return hebrew / (hebrew + latin) if hebrew + latin else 0.0


def detect_language(text: str) -> str | None:
    """'en', 'he' or 'mixed' by script ratio; None when the text has no letters in either script."""
    if not (_HEBREW.search(text) or _LATIN.search(text)):
        return None
    ratio = hebrew_ratio(text)
    if ratio >= 0.8:
        return "he"
    if ratio <= 0.2:
        return "en"
    return "mixed"


def session_language(texts: list[str]) -> str | None:
    """Majority language over a session's prompts; a tie is 'mixed'."""
    counts = Counter(lang for lang in map(detect_language, texts) if lang)
    if not counts:
        return None
    ranked = counts.most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return "mixed"
    return ranked[0][0]


def profile_language_v1(per_session_languages: list[str | None]) -> str | None:
    """Iteration 1 rule, kept so that run stays reproducible.

    One session: that language. Else the last two sessions if they agree, otherwise 'mixed'.
    """
    sessions = [lang for lang in per_session_languages if lang]
    if not sessions:
        return None
    if len(sessions) == 1:
        return sessions[0]
    return sessions[-1] if sessions[-1] == sessions[-2] else "mixed"


def session_vote(texts: list[str]) -> str | None:
    """A session's language: the majority of its voting prompts; a tie or no votes is no vote.

    A prompt whose own detection is 'mixed' (or that has no letters) casts no vote, so one pasted
    Hebrew passage inside an English prompt does not count.
    """
    counts = Counter(lang for lang in map(detect_language, texts) if lang in ("en", "he"))
    ranked = counts.most_common()
    if not ranked or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def profile_language_v2(session_votes: list[str | None], current: str | None) -> str | None:
    """Profile language from per-session votes (oldest first) and the current slot value.

    No current value: the first session vote wins. With one: of the last 4 votes, if both 'he'
    and 'en' appear at least twice the answer is 'mixed'; else if the last 2 votes agree on a
    language different from the current one, switch to it; else keep the current value.
    """
    votes = [v for v in session_votes if v]
    if not votes:
        return current
    if current is None:
        return votes[0]
    recent = Counter(votes[-4:])
    if recent["he"] >= 2 and recent["en"] >= 2:
        return "mixed"
    if len(votes) >= 2 and votes[-1] == votes[-2] and votes[-1] != current:
        return votes[-1]
    return current


_SENSITIVE_EN = re.compile(
    r"\b(mourning|mourner|shiva|shivah|sitting shiva|yahrzeit|"
    r"kaddish for my|died|passed away|funeral|"
    r"my (mother|father|wife|husband|son|daughter|brother|sister|grandmother|grandfather) (is )?"
    r"(dying|died|passed|sick)|"
    r"diagnos\w*|cancer|chemo\w*|terminal(ly)? ill|illness|"
    r"conversion|convert(ing)? to judaism|giyur|"
    r"mamzer|mamzerut|agunah|aguna|get refusal)\b",
    re.IGNORECASE,
)
_SENSITIVE_HE = re.compile(
    r"((יושב|יושבת|ישבתי|ישבנו|יושבים) שבעה|שבעה (על|שלי)|אחרי (ה)?שבעה|בימי השבעה|אבלות|נפטר|נפטרה|פטירת|קדיש על|יארצייט|"
    r"אובחנתי|אבחון|סרטן|מחלה קשה|מחלה|"
    r"גיור|התגיירות|ממזר|עגונה|מסורבת גט)"
)


def is_sensitive(text: str) -> bool:
    """True when the text touches mourning, illness, conversion or personal halakhic status."""
    return bool(_SENSITIVE_EN.search(text) or _SENSITIVE_HE.search(text))


class SensitiveScreen(Protocol):
    """Decides whether a free-text value may be stored. The service takes one, so a better
    screen can be plugged in. (A guardrail-based screen needs a change to the guardrail itself:
    `GuardrailResult` carries only `allowed` and `reason`.)"""

    def is_sensitive(self, text: str) -> bool: ...


class KeywordSensitiveScreen:
    """The only implementation: the prototype keyword lists above."""

    def is_sensitive(self, text: str) -> bool:
        return is_sensitive(text)


def value_is_sensitive(screen: SensitiveScreen, value: object) -> bool:
    """True when any free-text part of a slot value is sensitive."""
    if isinstance(value, str):
        return screen.is_sensitive(value)
    if isinstance(value, list):
        return any(value_is_sensitive(screen, item) for item in value)
    return False


# Matching too much is the accepted cost: a matched turn is classified, and one that cannot be
# classified is only quarantined. Hebrew words may carry the one-letter "and" prefix.
_INSTRUCTION = re.compile(
    r"\b(?:from now on|always|never|going forward|no more|ignore|skip|avoid|exclude"
    r"|hide|omit|filter|remove|leave out|keep away|(?:rather )?not see)\b"
    r"|\bdo(?:n['’]t| not)\b"
    r"|\bstop\s+\w+ing\b"
    r"|\bonly\s+(?:show|use|give|include|cite)\w*"
    r"|(?:^|[,;]\s*|\band\s+)without\b"
    r"|(?<!\w)ו?(?:מעכשיו|תמיד|אף פעם|לעולם|תפסיקי?|תתעלמי?|הסתר|תסתירי?|השמט|סנן)(?!\w)"
    r"|(?<!\w)ו?אל\s+(?!תוך(?!\w))ת\w+"
    r"|(?<!\w)ו?רק\s+(?:תראי?|תשתמשי?|תציגי?|תני|תן|תביאי?|להראות|להשתמש)(?!\w)"
    r"|(?:^|[,;]\s*|(?<!\w)ו)בלי(?!\w)",
    re.IGNORECASE,
)
MAX_DETECT_CHARS = 1000
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_JOIN = re.compile(r"(?:\b(?:and|but|then)|[,;])\s*$", re.IGNORECASE)
_TRAILING_JOIN = re.compile(r"\s*(?:\b(?:and|but|then)|[,;])\s*$", re.IGNORECASE)


def _clauses(sentence: str) -> list[str]:
    """Split one sentence into the instruction clauses it carries.

    Two instruction phrases are two instructions when words between them end in a join ("and",
    "but", a comma) or the second starts with the Hebrew "and" prefix; "from now on, always X" and
    "אף פעם אל תתרגם" stay one."""
    matches = list(_INSTRUCTION.finditer(sentence))
    if not matches:
        return []
    cuts = [0]
    for previous, match in zip(matches, matches[1:], strict=False):
        gap = sentence[previous.end() : match.start()]
        if re.search(r"\w", gap) and (_JOIN.search(gap) or match.group().startswith("ו")):
            cuts.append(match.start())
        elif match.group().startswith("ו"):
            cuts.append(match.start())
    cuts.append(len(sentence))
    clauses = (
        _TRAILING_JOIN.sub("", sentence[a:b]).strip(" ,;")
        for a, b in zip(cuts, cuts[1:], strict=False)
    )
    return [clause for clause in clauses if clause]


def find_standing_instructions(text: str) -> list[str]:
    """Every sentence or clause of `text` that carries a standing instruction (empty when none)."""
    found: list[str] = []
    # Prompts reach a model cut to this length, so detection looks at no more than that.
    for sentence in _SENTENCE_SPLIT.split(text[:MAX_DETECT_CHARS]):
        found.extend(_clauses(sentence))
    return found


# fmt: off
_BOOKS: dict[str, list[str]] = {
    "Tanakh": [
        "tanakh", "torah", "genesis", "exodus", "leviticus", "deuteronomy", "psalms", "isaiah",
        "jeremiah", "ezekiel", "proverbs", "ecclesiastes", "kohelet", "esther",
        "תנך", "תורה", "בראשית", "שמות", "ויקרא", "דברים", "תהילים", "ישעיהו", "ירמיהו",
        "משלי", "קהלת", "אסתר",
    ],
    "Mishnah": ["mishnah", "mishna", "pirkei avot", "משנה", "פרקי אבות"],
    "Talmud": [
        "talmud", "gemara", "bavli", "berakhot", "berachot", "shabbat", "eruvin", "pesachim",
        "yoma", "sukkah", "bava metzia", "bava kamma", "bava batra", "sanhedrin", "gittin",
        "kiddushin", "ketubot",
        "תלמוד", "גמרא", "ברכות", "שבת", "עירובין", "פסחים", "יומא", "סוכה", "בבא מציעא",
        "בבא קמא", "בבא בתרא", "סנהדרין", "גיטין", "קידושין", "כתובות",
    ],
    "Midrash": [
        "midrash", "genesis rabbah", "bereishit rabbah", "mekhilta", "sifra", "sifrei",
        "מדרש", "בראשית רבה", "מכילתא", "ספרא", "ספרי",
    ],
    "Halakhah": [
        "shulchan arukh", "shulchan aruch", "shulchan arukh", "mishneh torah", "halakhah",
        "halakha", "kitzur shulchan", "orach chayim",
        "שולחן ערוך", "משנה תורה", "הלכה", "קיצור שולחן ערוך", "אורח חיים",
    ],
    "Kabbalah": [
        "zohar", "kabbalah", "sefer yetzirah", "bahir", "etz chaim",
        "זוהר", "קבלה", "ספר יצירה", "הבהיר",
    ],
    "Chasidut": [
        "tanya", "chasidut", "hasidut", "likutei moharan", "sfat emet", "kedushat levi",
        "תניא", "חסידות", "ליקוטי מוהרן", "שפת אמת", "קדושת לוי",
    ],
    "Musar": [
        "mesillat yesharim", "mesilat yesharim", "chovot halevavot", "orchot tzaddikim", "musar",
        "מסילת ישרים", "חובות הלבבות", "אורחות צדיקים", "מוסר",
    ],
    "Liturgy": [
        "siddur", "machzor", "mahzor", "haggadah", "amidah", "prayer book",
        "סידור", "מחזור", "הגדה", "עמידה",
    ],
    "Jewish Thought": [
        "guide for the perplexed", "guide of the perplexed", "moreh nevukhim", "kuzari",
        "maharal", "jewish thought", "jewish philosophy",
        "מורה נבוכים", "כוזרי", "מהרל", "מחשבת ישראל",
    ],
    "Commentary": [
        "rashi", "tosafot", "ramban", "ibn ezra", "rashbam", "sforno",
        "רשי", "תוספות", "רמבן", "אבן עזרא", "רשבם", "ספורנו",
    ],
}
# fmt: on
_QUOTES = str.maketrans("", "", "\"'״׳’")
# Optional one-letter Hebrew prefix (in, to, the, and, ...) before a book name.
_BOOK_REGEX = r"(?<!\w)[בלהוכמש]?(?:{})(?!\w)"
_BOOK_PATTERNS = [
    (
        category,
        re.compile(
            _BOOK_REGEX.format("|".join(sorted(map(re.escape, names), key=len, reverse=True)))
        ),
    )
    for category, names in _BOOKS.items()
]


def cited_categories(text: str) -> list[str]:
    """Registry category names for the well-known books and corpora named in the text."""
    normalized = text.lower().translate(_QUOTES)
    found: dict[str, int] = {}
    for category, pattern in _BOOK_PATTERNS:
        match = pattern.search(normalized)
        if match:
            found[category] = match.start()
    return sorted(found, key=found.__getitem__)
