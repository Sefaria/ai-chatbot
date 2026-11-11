"""Every closed-set question (for Jev and for the small model), threshold and pinned setting for the hybrid_jev strategy, in one place.

Question rules: one factor per question; mutually exclusive answers are ONE choice with a
one-line description per option and an explicit `unclear` escape; "may match several or none" is
one noul per candidate; each asks for evidence a careful reader could point to in the state,
never a forecast or a verdict. The verdict is computed in code (see hybrid_jev.py).
"""

from __future__ import annotations

from ..jev import DEFAULT_MODEL, JevQuestion
from ..registry import SlotRegistry
from ..typed import TypedQuestion

JEV_MODEL = DEFAULT_MODEL  # pinned

# Thresholds. All are unfitted starting values; fitting each needs at least 100 labelled items
# per decision. A noul in 0.35-0.65 or a choice confidence under 0.5 counts as uncertain and
# must not act.
SET_MIN_CONFIDENCE = 0.5  # fill an empty slot
CHANGE_MIN_CONFIDENCE = 0.75  # replace an existing, different inferred value
CATEGORY_MIN_NOUL = 0.65
INSTRUCTION_MIN_NOUL = 0.65
WINDOW_PROMPTS = 12  # unfitted: how many recent prompts Jev sees
PROMPT_CHARS = 500  # unfitted: per-prompt truncation

UNCLEAR = "unclear"
CHOICE_SLOTS = ("hebrew_fluency", "terminology_comfort", "register")
CATEGORY_PREFIX = "category:"
STYLE_KEY = "is_lasting_style_preference"
EXCLUDE_KEY = "asks_to_exclude_sources"

_CHOICE_INSTRUCTIONS = {
    "hebrew_fluency": (
        "Based only on what the learner wrote in `learner_prompts`, how well does this learner "
        "read Hebrew?"
    ),
    "terminology_comfort": (
        "Based only on what the learner wrote in `learner_prompts`, how comfortable is this "
        "learner with Jewish study vocabulary such as sugya, gemara or remez?"
    ),
    "register": (
        "Based only on what the learner wrote in `learner_prompts`, which register of "
        "answers does this learner seem to want?"
    ),
}
_UNCLEAR_DESCRIPTION = "the prompts give no clear evidence either way"
_CHOICE_DESCRIPTIONS = {
    "hebrew_fluency": {
        "none": "says they cannot read Hebrew or asks for everything in English",
        "basic": "asks what single Hebrew words mean or asks for transliteration",
        "intermediate": "reads some Hebrew but asks for translation of longer passages",
        "fluent": "writes prompts in Hebrew or quotes Hebrew text they clearly understand",
    },
    "terminology_comfort": {
        "new": "asks what basic study terms mean",
        "some": "uses some study terms and asks about others",
        "comfortable": "uses study terms freely without asking what they mean",
    },
    "register": {
        "traditional": "studies inside the tradition: asks what the commentators say and how "
        "the sources are read in practice",
        "academic": "asks about dating, historical context, textual criticism or modern "
        "scholarship",
    },
}
CATEGORY_DESCRIPTIONS = {
    "Tanakh": "the Hebrew Bible: Torah, Prophets and Writings",
    "Mishnah": "the Mishnah, including Pirkei Avot",
    "Talmud": "the Babylonian or Jerusalem Talmud and its tractates",
    "Midrash": "rabbinic Midrash such as Bereshit Rabbah, Mekhilta or Sifra",
    "Halakhah": "Jewish law codes and responsa such as Mishneh Torah or Shulchan Arukh",
    "Kabbalah": "Kabbalistic works such as the Zohar or Sefer Yetzirah",
    "Chasidut": "Chasidic works such as Tanya, Likutei Moharan or Sfat Emet",
    "Musar": "ethical works such as Mesillat Yesharim or Chovot HaLevavot",
    "Liturgy": "the siddur, machzor, Haggadah and other prayer texts",
    "Jewish Thought": "Jewish philosophy and thought such as the Guide for the Perplexed or Kuzari",
    "Commentary": "classical commentators such as Rashi, Ramban, Ibn Ezra or Tosafot",
}


def choice_questions(registry: SlotRegistry) -> dict[str, JevQuestion]:
    """One choice per enum slot; options come from the registry so they cannot drift."""
    questions = {}
    for key in CHOICE_SLOTS:
        slot = registry.get(key)
        criteria = {v: _CHOICE_DESCRIPTIONS[key].get(v) for v in slot.allowed}
        criteria[UNCLEAR] = _UNCLEAR_DESCRIPTION
        questions[key] = JevQuestion("choice", _CHOICE_INSTRUCTIONS[key], criteria)
    return questions


def category_questions(registry: SlotRegistry) -> dict[str, JevQuestion]:
    """One noul per category: the prompts may match several or none."""
    return {
        f"{CATEGORY_PREFIX}{name}": JevQuestion(
            "noul",
            f"Do the prompts in `learner_prompts` ask about texts from {name}: "
            f"{CATEGORY_DESCRIPTIONS.get(name, name)}?",
        )
        for name in registry.get("text_categories").allowed
    }


def instruction_questions() -> dict[str, JevQuestion]:
    return {
        STYLE_KEY: JevQuestion(
            "noul",
            "Does `message` state a lasting preference for how future answers should be written: "
            "their length, format, language or level?",
        ),
        EXCLUDE_KEY: JevQuestion(
            "noul",
            "Does `message` ask to hide, exclude or filter out sources, authors, denominations "
            "or movements?",
        ),
    }


def to_typed(questions: dict[str, JevQuestion]) -> list[TypedQuestion]:
    return [
        TypedQuestion(key, jq.type, jq.instructions, jq.criteria) for key, jq in questions.items()
    ]


def thresholds() -> dict[str, float | int]:
    return {
        "SET_MIN_CONFIDENCE": SET_MIN_CONFIDENCE,
        "CHANGE_MIN_CONFIDENCE": CHANGE_MIN_CONFIDENCE,
        "CATEGORY_MIN_NOUL": CATEGORY_MIN_NOUL,
        "INSTRUCTION_MIN_NOUL": INSTRUCTION_MIN_NOUL,
        "WINDOW_PROMPTS": WINDOW_PROMPTS,
        "PROMPT_CHARS": PROMPT_CHARS,
    }
