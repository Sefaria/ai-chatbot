"""Metrics shared by the four `extract` cases: accuracy and over-inference on one slot group.

The core `slot_accuracy` and `over_inferred` count every slot. A case that asks about one kind of
fact ("on language and on text categories, each") needs the same ratios on one slot, so these two
metrics take the slot group as their parameter: `slot_accuracy_on@language`,
`over_inferred_on@text_categories`. The scoring is the core's own (`score_slot`), so a group
figure and the overall figure agree when the group is the only slot.
"""

from __future__ import annotations

from chat.V2.personalization.lab.metrics import Metric, Ratio
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
}  # fmt: skip
