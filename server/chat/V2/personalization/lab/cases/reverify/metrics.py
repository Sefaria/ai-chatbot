"""Metrics the `reverify` aspect adds to the core set: how far two labellers' golden profiles agree.

A labeller's golden profile gives, for each slot, a form: `{"value": ...}` (enum), `{"must_include":
[...], "any_include": [...]}` (list) or `{"any_of": [...]}` (free-text keywords), or null (empty).
`slot_code` compares two forms of one slot into one letter, so a case can keep the comparison of a
real persona without keeping either labeller's labels:

    A  both filled, same final value     D  both filled, different value
    F  only the first (frozen) labeller filled it     O  only the second labeller filled it
    B  both left it empty

"Same final value": an enum slot has the same `value` (`accept` lists are ignored); a list slot has
the same set of included categories (`must_include` plus `any_include`); a free-text slot has at least
one keyword in common (two labellers never write the same keyword list word for word).
A persona's comparison is one string of those letters, one per slot of the registry, in its order.
"""

from __future__ import annotations

from chat.V2.personalization.lab.metrics import Metric, Ratio, _slots

CODES = "ADFOB"


def _form(spec):
    """(kind, comparable) of a golden form, or None for an empty slot."""
    if not spec:
        return None
    if "value" in spec:
        return "enum", spec["value"]
    if "must_include" in spec or "any_include" in spec:
        return "list", frozenset(spec.get("must_include", []) + spec.get("any_include", []))
    return "text", frozenset(str(k).strip().lower() for k in spec.get("any_of", []))


def slot_code(first, second) -> str:
    a, b = _form(first), _form(second)
    if a is None and b is None:
        return "B"
    if b is None:
        return "F"
    if a is None:
        return "O"
    if a[0] == "text":
        same = bool(a[1] & b[1]) or a[1] == b[1]
    else:
        same = a[1] == b[1]
    return "A" if same else "D"


def agreement_codes(first: dict, second: dict) -> str:
    """One letter per registry slot, comparing two golden profiles."""
    return "".join(slot_code((first or {}).get(s), (second or {}).get(s)) for s in _slots())


def _rows(data, slot):
    """The comparison strings of the valid items, cut down to one slot when `slot` names one."""
    slots = _slots()
    picks = [slots.index(slot)] if slot else range(len(slots))
    for _frozen, codes in data.labels:
        if codes == "invalid" or len(codes) != len(slots):
            continue
        yield [codes[i] for i in picks]


def label_agreement(data, variant=None):
    agree = den = 0
    for row in _rows(data, variant):
        agree += row.count("A")
        den += len(row) - row.count("B")
    return Ratio(agree, den)


def label_both_empty_share(data, variant=None):
    empty = total = 0
    for row in _rows(data, variant):
        empty += row.count("B")
        total += len(row)
    return Ratio(empty, total)


METRICS = {
    "label_agreement": Metric(
        "label_agreement",
        "fraction",
        "slots where both labellers give the same final value ÷ slots that have a golden value in "
        "either labeller's profile (`label_agreement@<slot>` for one slot)",
        label_agreement,
    ),
    "label_both_empty_share": Metric(
        "label_both_empty_share",
        "fraction",
        "slots both labellers leave empty ÷ all slots of the labelled personas",
        label_both_empty_share,
    ),
}
