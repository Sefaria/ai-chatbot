"""Mock of aggressiveness/aggressive: three invented personas and a scripted stand-in model. One
prompt sets a slot, the next contradicting prompt replaces it; one odd prompt flips a settled value.
Asserts the exact metric values, the sample, the bars and the manifest as registered. No real text
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
    dest = tmp_path / "aggressiveness" / "aggressive"
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


# -- the arm: the latest confident signal wins --------------------------------------------------


def test_one_prompt_sets_a_slot_and_the_next_contradicting_prompt_replaces_it():
    reps, _llm = replays()
    assert fluency(reps["ana"]) == ["none", "fluent", "fluent", "fluent"]  # set at 1, replaced at 2


def test_one_odd_prompt_flips_a_settled_value_and_the_next_prompt_flips_it_back():
    reps, _llm = replays()
    assert fluency(reps["ben"]) == ["basic", "basic", "basic", "fluent", "basic"]


def test_the_sensitive_turn_never_reaches_the_model():
    reps, llm = replays()
    assert dict(reps["cal"].skips) == {"sensitive": 1}
    assert not any("mother died" in c["user"] for c in llm.calls)
    typed = [c for c in llm.calls if c["system"].startswith(TYPED)]
    assert len(typed) == 11  # 12 turns, the sensitive one dropped: one call per turn
    assert {c["model"] for c in llm.calls} == {MODEL}


# -- the exact metric values on the fixture ---------------------------------------------------------


def test_exact_metric_values_on_the_fixture(tmp_path):
    _case, result = mock_run(tmp_path)
    m = result["metrics"]
    assert m["turns_to_fill"] == 1  # median of [1, 2] (ana), [1, 1] (ben), [1] (cal)
    assert result["ratios"]["first_session_fill"] == [
        2,
        3,
    ]  # ana and ben; cal has no non-language slot
    assert m["first_session_fill"] == 2 / 3
    assert result["ratios"]["wrong_slot_turns"] == [1, 12]  # ben's fourth turn only
    assert m["wrong_slot_turns"] == 1 / 12
    assert (
        m["outlier_flips"] == 1
    )  # ben-4's flip; ben-5's flip back is not caused by an outlier turn
    assert result["ratios"]["flips_per_10_turns"] == [30, 12]  # three value changes in 12 turns
    assert m["flips_per_10_turns"] == 2.5
    assert result["invalid"] == 0 and result["privacy"]["sensitive_reached_model"] == 0
    assert result["privacy"]["stated_overwritten"] == 0


def test_fill_rate_at_k_reports_the_fixture_without_a_bar(tmp_path):
    _case, result = mock_run(tmp_path)
    assert result["metrics"]["fill_rate_at_k@2"] == 1.0  # ana and ben fill both slots by turn 2
    assert result["metrics"]["fill_rate_at_k@4"] == 1.0
    assert (
        result["metrics"]["run_agreement"] is None
    )  # a cross-run metric: no value on a single run


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


def test_manifest_has_the_rows_bars_the_unbarred_metrics_and_the_deviations():
    case = load_case(CASE_DIR)
    assert case.status == "planned" and case.engine == "offline" and case.task == "replay"
    assert case.datasets == ["syn1", "anon-personas"]
    assert (case.config["model"], case.config["min_support"], case.config["ratio"]) == (
        MODEL,
        1,
        0.0,
    )
    got = [(b.metric, b.agg, b.op, b.value, b.dataset, b.ref) for b in case.keep_if]
    assert got == [
        ("turns_to_fill", "mean", "<=", 2, "anon-personas", None),
        ("first_session_fill", "mean", ">=", 0.90, "anon-personas", None),
    ]  # the mistakes are the result: no bar on outlier_flips, flips_per_10_turns, wrong_slot_turns
    for name in (
        "outlier_flips",
        "flips_per_10_turns",
        "wrong_slot_turns",
        "fill_rate_at_k",
        "run_agreement",
    ):
        assert name in case.metrics and name not in {b.metric for b in case.keep_if}
    text = json.dumps(json.loads((CASE_DIR / "case.json").read_text())["deviations"])
    assert "Amendment A2" in text and "20261110" in text and "not human-reviewed" in text


def fake_result(run_no, median=1, fill=0.95):
    """A real-dataset result file as the runner writes it, with invented numbers."""
    return {
        "run": run_no, "dataset": {"name": "anon-personas", "kind": "real", "sha256": "s", "n": 20},
        "metrics": {"turns_to_fill": median, "first_session_fill": fill}, "ratios": {}, "invalid": 0,
        "privacy": {"sensitive_reached_model": None, "stated_overwritten": 0, "exclusion_stored": 0},
    }  # fmt: skip


def test_bars_keep_when_the_arm_fills_fast():
    case = load_case(CASE_DIR)
    out = verdict.compute(case, [fake_result(n) for n in (1, 2, 3)])
    assert out["verdict"] == "keep" and [b["state"] for b in out["bars"]] == ["holds", "holds"]


@pytest.mark.parametrize(
    "kwargs, failing",
    [({"median": 3}, [0]), ({"fill": 0.80}, [1]), ({"median": 4, "fill": 0.5}, [0, 1])],
)
def test_each_bar_fails_on_its_own(kwargs, failing):
    case = load_case(CASE_DIR)
    out = verdict.compute(case, [fake_result(n, **kwargs) for n in (1, 2, 3)])
    assert out["verdict"] == "drop"
    assert [i for i, b in enumerate(out["bars"]) if b["state"] == "fails"] == failing
