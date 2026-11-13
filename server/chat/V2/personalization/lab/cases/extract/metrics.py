"""Metrics shared by the four `extract` cases: accuracy and over-inference on one slot group.

The core `slot_accuracy` and `over_inferred` count every slot. A case that asks about one kind of
fact ("on language and on text categories, each") needs the same ratios on one slot, so these two
metrics take the slot group as their parameter: `slot_accuracy_on@language`,
`over_inferred_on@text_categories`. The scoring is the core's own (`score_slot`), so a group
figure and the overall figure agree when the group is the only slot.
"""

from __future__ import annotations

import dataclasses
import re

from chat.V2.personalization.lab.metrics import Metric, Ratio, over_inferred, slot_accuracy
from chat.V2.personalization.lab.scoring import score_slot

# group name (the metric's parameter) -> the registry slot it scores
GROUPS: dict[str, str] = {
    "language": "preferred_language",
    "text_categories": "text_categories",
}


def _slot_of(group) -> str:
    if group not in GROUPS:
        raise ValueError(f"unknown slot group {group!r}; one of {sorted(GROUPS)}")
    return GROUPS[group]


def slot_accuracy_on(data, group=None):
    slot = _slot_of(group)
    correct = scored = 0
    for replay in data.replays:
        golden = replay.persona["golden"].get(slot)
        if golden is None:
            continue
        verdict = score_slot(golden, replay.final_profile.get(slot))
        if verdict == "not_applicable":
            continue
        scored += 1
        correct += verdict == "correct"
    return Ratio(correct, scored)


def over_inferred_on(data, group=None):
    slot = _slot_of(group)
    filled = empty = 0
    for replay in data.replays:
        if replay.persona["golden"].get(slot) is None:
            empty += 1
            filled += replay.final_profile.get(slot) is not None
    return Ratio(filled, empty)


# -- the same two ratios per prompt language (Hebrew or English) -----------------------------------

LANGS = ("he", "en")
_HEBREW = re.compile(r"[\u0590-\u05ff]")
_LATIN = re.compile(r"[A-Za-z]")


def prompt_is_hebrew(text: str) -> bool:
    """A prompt is Hebrew when it holds more Hebrew-block letters than Latin letters."""
    return len(_HEBREW.findall(text)) > len(_LATIN.findall(text))


def persona_language(persona: dict) -> str:
    """`he` when more than half of the persona's prompts are Hebrew (see `prompt_is_hebrew`), else `en`.

    A tie, or a persona with no prompt, is `en`.
    """
    prompts = [t.get("user", "") for s in persona.get("sessions", []) for t in s.get("turns", [])]
    hebrew = sum(prompt_is_hebrew(p) for p in prompts)
    return "he" if prompts and hebrew * 2 > len(prompts) else "en"


def _in_language(data, lang):
    if lang not in LANGS:
        raise ValueError(f"unknown language {lang!r}; one of {list(LANGS)}")
    replays = [r for r in data.replays if persona_language(r.persona) == lang]
    return dataclasses.replace(data, replays=replays)


def slot_accuracy_by_lang(data, lang=None):
    return slot_accuracy(_in_language(data, lang))


def over_inferred_by_lang(data, lang=None):
    return over_inferred(_in_language(data, lang))


METRICS: dict[str, Metric] = {
    "slot_accuracy_on": Metric(
        "slot_accuracy_on", "fraction",
        "slot_accuracy for one slot group: personas whose final value for the group's slot equals "
        "the golden value ÷ personas that have a golden value for it (groups: language = "
        "preferred_language, text_categories = text_categories)",
        slot_accuracy_on, params=tuple(GROUPS),
    ),
    "over_inferred_on": Metric(
        "over_inferred_on", "fraction",
        "over_inferred for one slot group: personas whose final profile fills the group's slot "
        "where the golden profile leaves it empty ÷ personas whose golden profile leaves it empty",
        over_inferred_on, params=tuple(GROUPS),
    ),
    "slot_accuracy_by_lang": Metric(
        "slot_accuracy_by_lang", "fraction",
        "slot_accuracy over the personas of one prompt language: he = more than half of the "
        "persona's prompts hold more Hebrew letters than Latin letters, en = every other persona",
        slot_accuracy_by_lang, params=LANGS,
    ),
    "over_inferred_by_lang": Metric(
        "over_inferred_by_lang", "fraction",
        "over_inferred over the personas of one prompt language (he or en, as for slot_accuracy_by_lang)",
        over_inferred_by_lang, params=LANGS,
    ),
}  # fmt: skip
