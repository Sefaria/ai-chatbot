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


# ---- statements ABOUT THE USER in the never-store categories ------------------------------------
# Only first-person or possessive claims match, never a bare topic word: a study assistant sees
# "Reform", "convert", "mourner" and "wife" in ordinary questions about texts and law.

_IM = r"\bI(?:['’]?m| am)\b"
_KIN_EN = (
    r"(?:wife|husband|spouse|son|daughter|kids|children|mother|father|mom|dad|parents|brother|"
    r"sister|grandmother|grandfather|grandson|granddaughter|in-laws|girlfriend|boyfriend)"
)
_DENOM_EN = (
    r"(?:orthodox|reform|conservative|masorti|reconstructionist|haredi|charedi|hasidic|chasidic|"
    r"chabad|lubavitch\w*|litvish|secular|traditional|observant|religious|atheist|"
    r"shomer\s+(?:shabbat|shabbos|mitzvot)|dati|chiloni)"
)
_USER_EN = re.compile(
    "|".join(
        [
            # location
            r"\b(?:I|we)\s+(?:live|reside)\s+in\b",
            _IM + r"\s+(?:living|residing|based)\s+in\b",
            r"\bwe['’]re\s+(?:living|based)\s+in\b",
            # family
            rf"\bmy\s+{_KIN_EN}\b",
            _IM + r"\s+(?:married|divorced|widowed|a\s+(?:father|mother|dad|mom|parent))\b",
            r"\bI\s+have\s+(?:\w+\s+)?(?:kids|children|sons?|daughters?|grandchildren)\b",
            # health
            r"\bmy\s+(?:diagnosis|illness|cancer|chemo\w*|surgery|medical\s+condition|disease|"
            r"depression|anxiety|treatment)\b",
            r"\bI\s+(?:was|have\s+been|got)\s+diagnosed\b",
            _IM + r"\s+(?:sick(?!\s+of)|ill|unwell|in\s+(?:treatment|chemo\w*))\b",
            r"\bI\s+(?:have|got)\s+(?:cancer|diabetes|depression|anxiety|ptsd|an?\s+illness|"
            r"a\s+disease|a\s+chronic\s+\w+)\b",
            r"\bI\s+suffer\s+from\b",
            # mourning
            _IM + r"\s+(?:in\s+mourning|mourning|a\s+mourner|an\s+avel|sitting\s+shiv\w+|"
            r"observing\s+(?:shiva|yahrzeit))\b",
            rf"\bmy\s+{_KIN_EN}\s+(?:just\s+)?(?:died|passed(?:\s+away)?)\b",
            rf"\bI\s+(?:just\s+)?lost\s+my\s+{_KIN_EN}\b",
            r"\bI\s*(?:am|['’]m)?\s+saying\s+kaddish\b",
            # personal halakhic status
            _IM + r"\s+(?:a\s+|an\s+)?(?:kohen|cohen|levi|levite|mamzer|agunah|aguna|"
            r"bat\s+(?:kohen|levi))\b",
            r"\bmy\s+(?:husband|ex|ex-husband)\s+(?:refuses|won['’]?t)\s+(?:to\s+)?give\s+me\s+a\s+get\b",
            # conversion
            r"\bI\s+(?:have\s+)?converted\b",
            _IM + r"\s+(?:a\s+)?(?:converting|convert|ger|giyoret|ger\s+tzedek|jew\s+by\s+choice|"
            r"in\s+the\s+process\s+of\s+(?:converting|conversion|giyur))\b",
            r"\bmy\s+(?:conversion|giyur)\b",
            r"\bI\s+went\s+through\s+(?:a\s+)?(?:conversion|giyur)\b",
            # denomination or observance level
            _IM + r"\s+(?:an?\s+|not\s+)?(?:very\s+|fairly\s+|modern\s+|ultra[- ]|centrist\s+)?"
            rf"{_DENOM_EN}\b",
            r"\bI\s+(?:don['’]?t\s+)?(?:keep|observe)\s+(?:shabbat|shabbos|shabbes|kosher|"
            r"kashrut|the\s+sabbath)\b",
            rf"\bI\s+grew\s+up\s+{_DENOM_EN}\b",
        ]
    ),
    re.IGNORECASE,
)

# Hebrew has no usable \b next to letters, and one-letter prefixes (ב ל ה ו ש מ כ) attach to the
# word: a term may start after a non-letter or the start, with one optional prefix, and must not
# run into a following letter.
_HE_L = "א-ת"


def _he(words: str, prefixes: str = "וש") -> str:
    prefix = f"[{prefixes}]?" if prefixes else ""
    return rf"(?<![{_HE_L}]){prefix}(?:{words})(?![{_HE_L}])"


_I_HE = _he("אני|אנחנו|אנו")
_KIN_HE = "אבא|אמא|סבא|סבתא|אח|אחות|הבן|הבת|הבעל|האישה|האשה|הילד|הילדה|הילדים|הבנות|הבנים|ההורים"
_USER_HE = re.compile(
    "|".join(
        [
            # location
            _I_HE + r"\s+(?:גר|גרה|גרים|גרות|מתגורר|מתגוררת|מתגוררים)\s+ב",
            # family
            _he("אשתי", "בלמכוש"),
            _he(_KIN_HE) + r"\s+שלי",
            _he("בן\\s+זוגי|בת\\s+זוגי", "בלמכוש"),
            _I_HE + r"\s+(?:נשוי|נשואה|גרוש|גרושה|אלמן|אלמנה)" + rf"(?![{_HE_L}])",
            _I_HE + r"\s+(?:אבא|אמא|אב|אם)\s+(?:של|ל)" + rf"(?![{_HE_L}])",
            _he("יש\\s+לי")
            + r"\s+(?:\S+\s+)?"
            + _he("ילדים|ילד|ילדה|בנות|בן|בת|נכדים|נכד|אישה|אשה", ""),
            # health
            _he("יש\\s+לי") + r"\s+" + _he("סרטן|סכרת|דיכאון|חרדה|מחלה|גידול", ""),
            _I_HE + r"\s+(?:חולה|חולים|סובל|סובלת|מטופל|מטופלת)" + rf"(?![{_HE_L}])",
            _he("האבחנה|המחלה|הטיפול|הניתוח|הכימותרפיה") + r"\s+שלי",
            _he("עברתי", "וש") + r"\s+(?:ניתוח|כימותרפיה|הקרנות)",
            # mourning
            _I_HE + r"\s+(?:באבל|אבלה)" + rf"(?![{_HE_L}])",
            _I_HE + r"\s+אבל\s+על(?![" + _HE_L + "])",
            _I_HE + r"\s+(?:אומר|אומרת)\s+קדיש",
            _he("אשתי|בעלי|אחי|אחותי|בני|בתי|אמי|אבי", "בלמכוש")
            + r"\s+(?:נפטר|נפטרה|מת|מתה)(?!["
            + _HE_L
            + "])",
            _he(_KIN_HE) + r"\s+שלי\s+(?:נפטר|נפטרה|מת|מתה)",
            _he("איבדתי")
            + r"\s+(?:את\s+)?(?:אבא|אמא|אשתי|בעלי|אחי|אחותי|בני|בתי|הורה|ילד|ילדה|אח|אחות)",
            # personal halakhic status
            _I_HE
            + r"\s+(?:כהן|לוי|ממזר|ממזרת|עגונה|כהנת|מסורבת\s+גט|בת\s+כהן|בת\s+לוי)"
            + rf"(?![{_HE_L}])",
            r"מסרב\s+לתת\s+לי\s+גט",
            # conversion
            _he("התגיירתי"),
            _I_HE
            + r"\s+(?:מתגייר|מתגיירת|גר\s+צדק|גיורת|בגיור|בתהליך\s+(?:של\s+)?(?:גיור|התגיירות))"
            + rf"(?![{_HE_L}])",
            _he("עברתי") + r"\s+גיור",
            _he("הגיור") + r"\s+שלי",
            # denomination or observance level
            _I_HE + r"\s+(?:לא\s+)?(?:חרדי|חרדית|דתי|דתיה|דתייה|חילוני|חילונית|מסורתי|מסורתית|"
            r"רפורמי|רפורמית|קונסרבטיבי|קונסרבטיבית|אורתודוכסי|אורתודוקסי|אורתודוכסית|"
            r"אורתודוקסית|ליטאי|ליטאית|חסידי|חסידית|חב.?ד|חבדניק|חבדניקית|"
            r"שומר\s+שבת|שומרת\s+שבת|שומר\s+מצוות|שומרת\s+מצוות|שומר\s+כשרות|שומרת\s+כשרות)"
            + rf"(?![{_HE_L}])",
        ]
    )
)


def is_user_statement(text: str) -> bool:
    """True for a first-person or possessive statement about the user in a never-store category
    (health, mourning, family, personal halakhic status, conversion, denomination or observance
    level, location), in English or Hebrew. A bare topic word does not match."""
    return bool(_USER_EN.search(text) or _USER_HE.search(text))


def is_sensitive(text: str) -> bool:
    """True when the text touches mourning, illness, conversion or personal halakhic status."""
    return bool(_SENSITIVE_EN.search(text) or _SENSITIVE_HE.search(text) or is_user_statement(text))


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
        "shulchan arukh", "shulchan aruch", "mishneh torah", "halakhah",
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
