"""Mock of extract/llm-free: five invented personas with string turn ids, a scripted stand-in model
(replies per turn, including malformed ones), the exact metric values, the one-call-per-turn shape,
the outlier-flip count, the sample and the bars. No real text and no model call."""

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
from chat.V2.personalization.strategies.prompts import REALTIME_SYSTEM

CASE_DIR = Path(__file__).parent
MODEL = "claude-haiku-4-5-20251001"


def case_copy(tmp_path):
    """The case without its results, in a directory outside the repository (with its aspect files)."""
    dest = tmp_path / "extract" / "llm-free"
    shutil.copytree(CASE_DIR, dest, ignore=shutil.ignore_patterns("results", "__pycache__"))
    for name in ("aspect.json", "metrics.py"):
        shutil.copy(CASE_DIR.parent / name, dest.parent)
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
    out = {}
    for persona in mod.mock_items():
        out[persona["id"]] = replay_persona(mod.build(case.config, llm), persona)
    return out, llm


def profile(replay):
    return {k: v["value"] for k, v in replay.final_profile.items()}


def turn_values(replay, slot):
    return [
        (s["turn_id"], (s["profile"].get(slot) or {}).get("value"))
        for s in replay.snapshots
        if s["kind"] == "turn"
    ]


# -- each scripted reply on its persona -----------------------------------------------------------


def test_each_persona_ends_with_what_the_scripted_replies_allow():
    got = {pid: profile(r) for pid, r in replays()[0].items()}
    assert got == {
        "ava": {"preferred_language": "en", "hebrew_fluency": "none",
                "terminology_comfort": "new", "text_categories": ["Tanakh"]},
        "ben": {"preferred_language": "en", "hebrew_fluency": "basic"},  # flipped to fluent and back
        "cal": {"preferred_language": "en", "terminology_comfort": "new",
                "text_categories": ["Talmud"]},
        "dee": {"preferred_language": "en"},  # nothing the model said was usable
        "eve": {"preferred_language": "en", "text_categories": ["Talmud"],
                "terminology_comfort": "comfortable"},  # two fills the golden profile leaves empty
    }  # fmt: skip


def test_free_form_writes_at_once_and_a_later_turn_rewrites_the_slot():
    ben = replays()[0]["ben"]
    assert turn_values(ben, "hebrew_fluency") == [
        ("ben-t1", "basic"),
        ("ben-t2", "fluent"),
        ("ben-t3", "basic"),
    ]  # no threshold: one turn writes, the next turn overwrites


def test_a_malformed_or_out_of_options_reply_changes_nothing():
    reps, _llm = replays()
    dee = reps["dee"]
    assert profile(dee) == {"preferred_language": "en"}
    assert dee.final_profile.get("hebrew_fluency") is None  # plain text, a wrong-shaped object,
    # and "native", which is not an option of hebrew_fluency: none of them is written


def test_the_sensitive_turn_and_the_instruction_never_reach_the_model():
    reps, llm = replays()
    assert dict(reps["eve"].skips) == {"sensitive": 1, "instruction_quarantined": 1}
    sent = " ".join(c["user"] for c in llm.calls)
    assert "mother died" not in sent and "From now on" not in sent


# -- the call: one free-form call per turn, on the pinned model -----------------------------------


def test_one_call_per_turn_on_the_pinned_model_with_the_profile_and_the_turn():
    _reps, llm = replays()
    assert len(llm.calls) == 14  # 16 turns, the sensitive one and the instruction never sent
    assert {c["model"] for c in llm.calls} == {MODEL}
    assert all(c["system"].startswith(REALTIME_SYSTEM[:60]) for c in llm.calls)
    first, second = llm.calls[0]["user"], llm.calls[1]["user"]
    assert "Current profile:" in first and "Learner: Please explain this week's parsha" in first
    assert (
        '"hebrew_fluency"' in second
    )  # the profile the first reply wrote is shown on the next turn
    assert "Show me the Talmud page" not in first  # one turn only, never a window of prompts


# -- the exact metric values on the fixture --------------------------------------------------------


def test_exact_metric_values_on_the_fixture(tmp_path):
    _case, result = mock_run(tmp_path)
    m = result["metrics"]
    assert result["ratios"]["slot_accuracy"] == [11, 12]  # all but dee's hebrew_fluency
    assert m["slot_accuracy"] == 11 / 12
    assert result["ratios"]["over_inferred"] == [2, 23]  # eve's text_categories and terminology
    assert m["over_inferred"] == 2 / 23
    assert m["slot_accuracy_on@language"] == 1.0 and m["slot_accuracy_on@text_categories"] == 1.0
    assert m["over_inferred_on@text_categories"] == 1 / 3  # eve; ben and dee left it empty
    assert result["ratios"]["first_session_fill"] == [3, 5]  # ava, ben and cal
    assert m["sensitive_dropped_share"] == 1 / 16
    assert result["ratios"]["flips_per_10_turns"] == [30, 16]  # ben twice, cal once, in 16 turns
    assert m["run_agreement"] is None  # a cross-run metric: no value on a single run
    assert result["dataset"]["n"] == 5 and result["invalid"] == 0


def test_outlier_flips_count_changes_not_first_fills_with_string_turn_ids(tmp_path):
    _case, result = mock_run(tmp_path)
    # ben-t2 flips basic to fluent (counted); ben-t3 flips it back (not tagged);
    # cal-t1 is a tagged first fill (not a flip); cal-t2 flips comfortable to new (counted)
    assert result["metrics"]["outlier_flips"] == 2
    reps, _llm = replays()
    ids = {t["id"] for s in reps["ben"].persona["sessions"] for t in s["turns"]}
    assert ids == {"ben-t1", "ben-t2", "ben-t3"}  # strings, as in the A1 labels
    data = RunData(replays=[reps["ben"]])
    assert evaluate(METRICS["outlier_flips"], data)[0] == 1


def test_calls_are_counted_and_cost_something(tmp_path):
    _case, result = mock_run(tmp_path)
    assert result["cost"]["calls"] == 14 and result["cost"]["typed_requests"] == 0
    assert result["metrics"]["calls_per_turn"] == 14 / 16
    assert result["cost"]["usd"] > 0
    assert result["privacy"]["sensitive_reached_model"] == 0
    assert result["privacy"]["stated_overwritten"] == 0


def test_run_agreement_is_one_when_the_profiles_do_not_change_between_runs():
    reps, _llm = replays()
    same = {r.persona["user_id"]: profile(r) for r in reps.values()}
    data = RunData(replays=list(reps.values()), prior_profiles=[same, same])
    assert evaluate(METRICS["run_agreement"], data)[0] == 1.0
    moved = {**same, "mock:ava": {**same["mock:ava"], "hebrew_fluency": "basic"}}
    data = RunData(replays=list(reps.values()), prior_profiles=[same, moved])
    assert evaluate(METRICS["run_agreement"], data)[0] < 1.0


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


def test_manifest_has_the_rows_bars_on_anon_personas_and_the_deviations():
    case = load_case(CASE_DIR)
    assert case.status == "planned" and case.engine == "offline" and case.task == "replay"
    assert case.datasets == ["syn1", "anon-personas"]
    assert case.config["strategy"] == "realtime" and case.config["model"] == MODEL
    got = [(b.metric, b.agg, b.op, b.value, b.dataset, b.expected_to_fail) for b in case.keep_if]
    assert got == [
        ("slot_accuracy", "mean", ">=", 0.85, "anon-personas", False),
        ("slot_accuracy", "min", ">=", 0.80, "anon-personas", True),  # the row expects it to fail
        ("outlier_flips", "pooled", "<=", 1, "anon-personas", False),
    ]
    text = json.dumps(json.loads((CASE_DIR / "case.json").read_text())["deviations"])
    assert "Amendment A2" in text and "20261110" in text and "not human-reviewed" in text


def fake_result(run_no, accuracy=0.90, minimum=None, flips=0):
    """A real-dataset result file as the runner writes it, with invented numbers."""
    return {
        "run": run_no, "dataset": {"name": "anon-personas", "kind": "real", "sha256": "s", "n": 20},
        "metrics": {"slot_accuracy": minimum if (minimum and run_no == 3) else accuracy,
                    "outlier_flips": flips},
        "ratios": {}, "invalid": 0,
        "privacy": {"sensitive_reached_model": None, "stated_overwritten": 0, "exclusion_stored": 0},
    }  # fmt: skip


def test_keep_when_the_mean_holds_and_the_expected_failure_is_not_counted():
    case = load_case(CASE_DIR)
    # runs 0.95, 0.95, 0.76: mean 0.887 holds; the min bar (0.76 < 0.80) fails but is expected to
    out = verdict.compute(case, [fake_result(n, accuracy=0.95, minimum=0.76) for n in (1, 2, 3)])
    states = {(b["agg"], b["metric"]): b for b in out["bars"]}
    assert out["verdict"] == "keep"
    assert states[("min", "slot_accuracy")]["expected_to_fail"] is True
    assert states[("min", "slot_accuracy")]["holds"] is False


@pytest.mark.parametrize(
    "kwargs, failing",
    [
        ({"accuracy": 0.82}, [0]),  # mean under 0.85 (the expected min bar is not counted)
        ({"flips": 1}, [2]),  # one flip in each run pools to 3, over the bar's 1
    ],
)
def test_each_counted_bar_fails_on_its_own(kwargs, failing):
    case = load_case(CASE_DIR)
    out = verdict.compute(case, [fake_result(n, **kwargs) for n in (1, 2, 3)])
    assert out["verdict"] == "drop"
    assert [i for i, b in enumerate(out["bars"]) if b["state"] == "fails"] == failing


def test_one_outlier_flip_in_all_three_runs_together_holds_the_pooled_bar():
    case = load_case(CASE_DIR)
    out = verdict.compute(case, [fake_result(n, flips=1 if n == 2 else 0) for n in (1, 2, 3)])
    assert out["verdict"] == "keep" and out["bars"][2]["observed"] == 1
