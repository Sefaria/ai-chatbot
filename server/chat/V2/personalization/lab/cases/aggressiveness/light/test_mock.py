"""Mock of aggressiveness/light: three invented personas and a scripted stand-in model. 4 signals for
one value and 1 for another activate the first; 2 against 2 activate neither; 4 more for the rival
activate or flip it; one odd prompt changes nothing. Asserts the exact metric values, the sample, the bars and the manifest as registered. No real text
and no model call."""

import json
import random
import shutil
from pathlib import Path

import pytest

from chat.V2.personalization.lab import results, runner, verdict
from chat.V2.personalization.lab.datasets import Dataset
from chat.V2.personalization.lab.manifest import load_case
from chat.V2.personalization.lab.metrics import METRICS, RunData, evaluate
from chat.V2.personalization.lab.replay import replay_persona

CASE_DIR = Path(__file__).parent
MODEL = "claude-haiku-4-5-20251001"
TYPED = "You answer closed-set questions"


def case_copy(tmp_path):
    """The case without its results, in a directory outside the repository (with its aspect file)."""
    dest = tmp_path / "aggressiveness" / "light"
    shutil.copytree(CASE_DIR, dest, ignore=shutil.ignore_patterns("results", "__pycache__"))
    shutil.copy(CASE_DIR.parent / "aspect.json", dest.parent)
    return dest


def module():
    return runner.load_pipeline(CASE_DIR)


def mock_run(tmp_path, items=None, kind="synthetic", **kw):
    case = load_case(case_copy(tmp_path))
    mod = runner.load_pipeline(case.dir)
    items = items if items is not None else mod.mock_items()
    dataset = Dataset("mock", kind, "sha", items, **kw)
    return case, runner.run_case(
        case, kind="mock", run_no=0, dataset=dataset, llm=mod.mock_llm(), typed=None
    )


def replays():
    mod, case = module(), load_case(CASE_DIR)
    llm = mod.mock_llm()
    out = {p["id"]: replay_persona(mod.build(case.config, llm), p) for p in mod.mock_items()}
    return out, llm


def fluency(replay):
    return [
        (s["profile"].get("hebrew_fluency") or {}).get("value") for s in replay.turn_snapshots()
    ]


# -- the arm: 3 signals and twice any rival's support ------------------------------------------------


def test_four_signals_against_one_activate_the_first_value():
    reps, _llm = replays()
    # none, fluent x4: 3 fluent against 1 none at turn 4 is the first time both tests pass
    assert fluency(reps["ana"]) == [None, None, None, "fluent", "fluent"]


def test_two_against_two_activate_neither_and_four_more_for_the_rival_activate_it():
    reps, _llm = replays()
    # basic, fluent, basic, fluent (a tie), then fluent x4: 3 against 2 is not twice, 4 against 2 is
    assert fluency(reps["ben"]) == [None, None, None, None, None, "fluent", "fluent", "fluent"]


def test_one_odd_prompt_changes_nothing_and_a_rival_with_twice_the_support_flips_the_value():
    reps, _llm = replays()
    # fluent x3 active at turn 3; the odd "none" (turn 4) and four more leave 3 against 5 (not twice);
    # the sixth "none" is 6 against 3: it flips at turn 9
    assert fluency(reps["cal"]) == [
        None,
        None,
        "fluent",
        "fluent",
        "fluent",
        "fluent",
        "fluent",
        "fluent",
        "none",
    ]


def test_each_turn_is_one_typed_call_on_the_pinned_model():
    _reps, llm = replays()
    typed = [c for c in llm.calls if c["system"].startswith(TYPED)]
    assert len(typed) == 22 and {c["model"] for c in llm.calls} == {MODEL}


# -- the exact metric values on the fixture ---------------------------------------------------------


def test_exact_metric_values_on_the_fixture(tmp_path):
    _case, result = mock_run(tmp_path)
    m = result["metrics"]
    # turns to fill: ana [language 3, fluency 4], ben [3, 6], cal [3, 9]
    assert m["turns_to_fill"] == 3.5
    assert result["ratios"]["slot_accuracy"] == [6, 6] and m["slot_accuracy"] == 1.0
    assert result["ratios"]["first_session_fill"] == [3, 3]
    assert result["ratios"]["wrong_slot_turns"] == [
        4,
        22,
    ]  # cal's turns 5 to 8: fluent while the learner is now "none"
    assert m["wrong_slot_turns"] == 4 / 22
    assert m["outlier_flips"] == 0  # the odd prompt (cal-4) changed nothing
    assert result["ratios"]["flips_per_10_turns"] == [10, 22]  # one value change: cal's, at turn 9
    assert result["invalid"] == 0 and result["privacy"]["sensitive_reached_model"] == 0
    assert result["privacy"]["stated_overwritten"] == 0


def test_run_agreement_is_one_when_the_profiles_do_not_change_between_runs():
    reps, _llm = replays()
    same = {
        r.persona["user_id"]: {k: v["value"] for k, v in r.final_profile.items()}
        for r in reps.values()
    }
    data = RunData(replays=list(reps.values()), prior_profiles=[same, same])
    assert evaluate(METRICS["run_agreement"], data)[0] == 1.0


# -- the A2 seeded sample ---------------------------------------------------------------------------


def invented_personas(n=30):
    return [{"user_id": f"mock:{i:03d}", "sessions": []} for i in range(n)]


def test_the_sample_is_20_personas_fixed_by_the_seed_in_case_json():
    config = load_case(CASE_DIR).config
    assert (config["sample_dataset"], config["sample_size"], config["sample_seed"]) == (
        "anon-personas", 20, 20261110,
    )  # fmt: skip
    items = invented_personas()
    real = Dataset("anon-personas", "real", "sha", items, anonymized=True)
    picked = module().select_items(items, config, real)
    ids = sorted(i["user_id"] for i in items)
    assert [p["user_id"] for p in picked] == sorted(random.Random(20261110).sample(ids, 20))
    assert module().select_items(list(reversed(items)), config, real) == picked


def test_syn1_and_smaller_datasets_are_not_sampled():
    config = load_case(CASE_DIR).config
    items = invented_personas()
    assert (
        len(module().select_items(items, config, Dataset("syn1", "synthetic", "sha", items))) == 30
    )
    small = Dataset("anon-personas", "real", "sha", items[:7], anonymized=True)
    assert len(module().select_items(items[:7], config, small)) == 7


def test_a_run_on_anon_personas_carries_numbers_only(tmp_path, monkeypatch):
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(tmp_path / "la-lab-data"))
    _case, result = mock_run(tmp_path, kind="real", anonymized=True)
    results.check_real(result)  # raises on per-item rows or free text
    assert "per_item" not in result


# -- the manifest as registered ---------------------------------------------------------------------

REF = {
    "case": "aggressiveness/aggressive",
    "metric": "wrong_slot_turns",
    "agg": "mean",
    "factor": 0.5,
}


def test_manifest_has_the_rows_bars_the_reference_bar_and_the_deviations():
    case = load_case(CASE_DIR)
    assert case.status == "planned" and case.engine == "offline" and case.task == "replay"
    assert case.datasets == ["syn1", "anon-personas"]
    assert (case.config["model"], case.config["min_support"], case.config["ratio"]) == (
        MODEL,
        3,
        2.0,
    )
    got = [(b.metric, b.agg, b.op, b.value, b.dataset, b.ref) for b in case.keep_if]
    assert got == [
        ("wrong_slot_turns", "mean", "<=", None, "anon-personas", REF),
        ("outlier_flips", "pooled", "==", 0, "anon-personas", None),
        ("turns_to_fill", "mean", "<=", 5, "anon-personas", None),
        ("slot_accuracy", "mean", ">=", 0.85, "anon-personas", None),
    ]
    text = json.dumps(json.loads((CASE_DIR / "case.json").read_text())["deviations"])
    assert "Amendment A2" in text and "20261110" in text and "not human-reviewed" in text


def fake_result(run_no, wrong=0.05, outliers=0, median=3.5, accuracy=0.90):
    """A real-dataset result file as the runner writes it, with invented numbers."""
    return {
        "run": run_no, "dataset": {"name": "anon-personas", "kind": "real", "sha256": "s", "n": 20},
        "metrics": {"wrong_slot_turns": wrong, "outlier_flips": outliers, "turns_to_fill": median,
                    "slot_accuracy": accuracy},
        "ratios": {}, "invalid": 0,
        "privacy": {"sensitive_reached_model": None, "stated_overwritten": 0, "exclusion_stored": 0},
    }  # fmt: skip


def aggressive_runs(wrong):
    return lambda case, ref_id, n: (
        fake_result(n, wrong=wrong) if ref_id == "aggressiveness/aggressive" else None
    )


def states(out):
    return [b["state"] for b in out["bars"]]


def test_bars_keep_when_light_has_at_most_half_the_wrong_turns_of_aggressive():
    case = load_case(CASE_DIR)
    out = verdict.compute(
        case, [fake_result(n) for n in (1, 2, 3)], ref_reader=aggressive_runs(0.10)
    )
    assert out["verdict"] == "keep" and states(out) == ["holds"] * 4
    assert out["bars"][0]["target"] == 0.05  # half of aggressive's mean


def test_the_reference_bar_fails_when_light_is_not_half_as_wrong_as_aggressive():
    case = load_case(CASE_DIR)
    out = verdict.compute(
        case, [fake_result(n) for n in (1, 2, 3)], ref_reader=aggressive_runs(0.08)
    )
    assert out["verdict"] == "drop" and states(out) == ["fails", "holds", "holds", "holds"]


def test_without_aggressive_results_the_reference_bar_is_inconclusive():
    case = load_case(CASE_DIR)
    out = verdict.compute(
        case, [fake_result(n) for n in (1, 2, 3)], ref_reader=lambda c, r, n: None
    )
    assert states(out)[0] == "inconclusive" and out["verdict"] == "inconclusive"


@pytest.mark.parametrize(
    "kwargs, failing",
    [({"outliers": 1}, [1]), ({"median": 6}, [2]), ({"accuracy": 0.80}, [3])],
)
def test_each_other_bar_fails_on_its_own(kwargs, failing):
    case = load_case(CASE_DIR)
    out = verdict.compute(
        case, [fake_result(n, **kwargs) for n in (1, 2, 3)], ref_reader=aggressive_runs(0.10)
    )
    assert out["verdict"] == "drop"
    assert [i for i, s in enumerate(states(out)) if s == "fails"] == failing
