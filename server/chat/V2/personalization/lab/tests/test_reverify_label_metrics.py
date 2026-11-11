"""The reverify aspect's label-agreement metrics, on invented comparison strings."""

from chat.V2.personalization.lab.metrics import CASES_ROOT, METRICS, RunData, _slots, evaluate
from chat.V2.personalization.lab.metrics import load_aspect_metrics as load

ASPECT = load(CASES_ROOT / "reverify" / "metrics.py")


def codes(**by_slot):
    return "".join(by_slot.get(s, "B") for s in _slots())


def run(*rows):
    return RunData(labels=[("golden", r) for r in rows])


def val(name, data, variant=None):
    return evaluate(ASPECT[name], data, variant)[0]


def slot_code():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "reverify_metrics_t", CASES_ROOT / "reverify" / "metrics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_slot_forms_compare_by_kind():
    m = slot_code()
    assert m.slot_code({"value": "en", "accept": ["he"]}, {"value": "en"}) == "A"
    assert m.slot_code({"value": "en"}, {"value": "he"}) == "D"
    assert (
        m.slot_code(
            {"must_include": ["Tanakh"], "any_include": ["Talmud"]},
            {"must_include": ["Talmud", "Tanakh"]},
        )
        == "A"
    )
    assert m.slot_code({"must_include": ["Tanakh"]}, {"must_include": ["Tanakh", "Talmud"]}) == "D"
    assert m.slot_code({"any_of": ["short", "source"]}, {"any_of": ["Short"]}) == "A"
    assert m.slot_code({"any_of": ["short"]}, {"any_of": ["long"]}) == "D"
    assert m.slot_code({"value": "en"}, None) == "F"
    assert m.slot_code(None, {"value": "en"}) == "O"
    assert m.slot_code(None, {}) == "B"


def test_agreement_codes_have_one_letter_per_slot():
    m = slot_code()
    assert m.agreement_codes({}, {}) == "B" * len(_slots())


def test_label_agreement_is_agreeing_slots_over_slots_filled_by_either():
    s = _slots()
    row = codes(**{s[0]: "A", s[1]: "A", s[2]: "D", s[3]: "F"})  # 4 filled, 2 agree
    ratio = ASPECT["label_agreement"].compute(run(row), None)
    assert (ratio.num, ratio.den) == (2, 4)
    assert val("label_agreement", run(row)) == 0.5


def test_per_slot_and_both_empty_share_and_invalid_items():
    s = _slots()
    a = codes(**{s[0]: "A", s[1]: "D"})
    b = codes(**{s[0]: "D"})
    data = RunData(labels=[("golden", a), ("golden", b), ("golden", "invalid")])
    assert val("label_agreement", data, s[0]) == 0.5
    assert val("label_agreement", data, s[1]) == 0.0
    assert val("label_agreement", data, s[2]) is None  # nobody filled it
    share = ASPECT["label_both_empty_share"].compute(data, None)
    assert (share.num, share.den) == (len(s) * 2 - 3, len(s) * 2)


def test_metrics_have_formulas_and_are_in_the_registry():
    assert all(m.formula for m in ASPECT.values())
    assert {"label_agreement", "label_both_empty_share"} <= set(METRICS)
