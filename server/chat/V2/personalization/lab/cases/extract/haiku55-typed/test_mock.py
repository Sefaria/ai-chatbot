"""Mock of extract/haiku55-typed: five invented personas, a scripted stand-in model (replies per turn,
including malformed ones), the exact metric values, the 12-prompt window, the sample and the bars.
No real text and no model call."""

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
MODEL = "claude-haiku-5-5"


def case_copy(tmp_path):
    """The case without its results, in a directory outside the repository (with its aspect files)."""
    dest = tmp_path / "extract" / "haiku55-typed"
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
        pipeline = mod.build(case.config, llm)
        out[persona["id"]] = replay_persona(pipeline, persona)
    return out, llm


def profile(replay):
    return {k: v["value"] for k, v in replay.final_profile.items()}


# -- each scripted reply on its persona -----------------------------------------------------------


def test_each_persona_ends_with_what_the_scripted_replies_allow():
    got = {pid: profile(r) for pid, r in replays()[0].items()}
    assert got == {
        "ava": {"preferred_language": "en", "hebrew_fluency": "none",
                "terminology_comfort": "new", "text_categories": ["Tanakh"]},
        "ben": {"preferred_language": "he", "hebrew_fluency": "fluent",
                "terminology_comfort": "comfortable", "text_categories": ["Talmud"]},
        "cal": {"preferred_language": "en", "hebrew_fluency": "basic"},  # kept through two bad replies
        "dee": {"preferred_language": "en"},  # both replies malformed: only the code-set language
        "eve": {"preferred_language": "en", "terminology_comfort": "comfortable"},  # the wrong fill
    }  # fmt: skip


def test_a_malformed_reply_scores_as_no_change():
    reps, llm = replays()
    cal = reps["cal"]
    assert [s["profile"].get("hebrew_fluency", {}).get("value") for s in cal.turn_snapshots()] == [
        "basic",
        "basic",
        "basic",
    ]  # valid reply, then plain text, then a wrong-shaped object: the value never moves or clears
    assert profile(reps["dee"]) == {"preferred_language": "en"}  # nothing the model said was used
    assert (
        reps["eve"].final_profile.get("hebrew_fluency") is None
    )  # confidence 0.3 and 0.4 act on nothing
    assert "text_categories" not in profile(
        reps["eve"]
    )  # scores 0.5 and 0.6 are uncertain: no fill


def test_the_sensitive_turn_never_reaches_the_model():
    reps, llm = replays()
    assert dict(reps["eve"].skips) == {"sensitive": 1}
    assert not any("mother died" in c["user"] for c in llm.calls)


# -- the call: closed-set questions, the last 12 prompts, the pinned model -------------------------


def test_one_call_per_turn_asks_the_closed_set_over_the_pinned_model():
    _reps, llm = replays()
    typed = [c for c in llm.calls if c["system"].startswith("You answer closed-set questions")]
    assert len(typed) == 13  # 14 turns, the sensitive one dropped
    assert {c["model"] for c in typed} == {MODEL}
    assert {c["model"] for c in llm.calls} == {MODEL}
    asked = typed[0]["user"]
    for key in (
        "hebrew_fluency",
        "terminology_comfort",
        "register",
        "category:Tanakh",
        "category:Talmud",
    ):
        assert f'- key "{key}"' in asked
    assert "unclear" in asked


def test_the_state_holds_the_last_12_prompts_only():
    mod, case = module(), load_case(CASE_DIR)
    turns = [
        {"id": f"zed-{i}", "at": f"2026-11-04T09:{i:02d}:00Z", "user": f"Question number {i} about Genesis",
         "assistant": "ok", "tags": []}
        for i in range(1, 15)
    ]  # fmt: skip
    persona = {"id": "zed", "user_id": "mock:zed", "golden": {}, "checks": [], "interview": None,
               "sessions": [{"session_id": "zed-s1", "turns": turns}]}  # fmt: skip
    llm = mod.mock_llm()
    replay_persona(mod.build(case.config, llm), persona)
    typed = [c for c in llm.calls if c["system"].startswith("You answer closed-set questions")]
    assert len(typed) == 14
    state = json.loads(typed[-1]["user"].split("<state>\n", 1)[1].split("\n</state>", 1)[0])
    texts = [p["text"] for p in state["learner_prompts"]]
    assert len(texts) == 12
    assert (
        texts[0] == "Question number 3 about Genesis"
        and texts[-1] == "Question number 14 about Genesis"
    )


# -- the exact metric values on the fixture --------------------------------------------------------


def test_exact_metric_values_on_the_fixture(tmp_path):
    _case, result = mock_run(tmp_path)
    m = result["metrics"]
    assert result["ratios"]["slot_accuracy"] == [
        12,
        14,
    ]  # ava 4, ben 4, cal 2, dee 1, eve 1 of 14 golden slots
    assert m["slot_accuracy"] == 12 / 14
    assert result["ratios"]["over_inferred"] == [
        1,
        21,
    ]  # eve's terminology_comfort, of 21 empty slots
    assert m["over_inferred"] == 1 / 21
    assert result["ratios"]["first_session_fill"] == [3, 5]  # ava, ben and cal; not dee, not eve
    assert m["first_session_fill"] == 0.6
    assert m["slot_accuracy_on@language"] == 1.0
    assert (
        m["slot_accuracy_on@text_categories"] == 2 / 3
    )  # ava and ben; dee's replies were malformed
    assert m["over_inferred_on@text_categories"] == 0.0
    assert m["sensitive_dropped_share"] == 1 / 14
    assert m["run_agreement"] is None  # a cross-run metric: no value on a single run
    assert result["dataset"]["n"] == 5 and result["invalid"] == 0


def test_calls_are_counted_as_typed_requests_and_cost_something(tmp_path):
    _case, result = mock_run(tmp_path)
    assert result["cost"]["typed_requests"] == 13
    assert result["cost"]["calls"] == 18  # 13 typed + the nightly batch once per persona
    assert result["metrics"]["calls_per_turn"] == 18 / 14
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
    assert case.config["strategy"] == "hybrid_haiku" and case.config["model"] == MODEL
    got = [(b.metric, b.agg, b.op, b.value, b.dataset) for b in case.keep_if]
    assert got == [
        ("slot_accuracy", "mean", ">=", 0.85, "anon-personas"),
        ("slot_accuracy", "min", ">=", 0.80, "anon-personas"),
        ("over_inferred", "mean", "<=", 0.05, "anon-personas"),
        ("first_session_fill", "mean", ">=", 0.75, "anon-personas"),
        ("run_agreement", "mean", ">=", 0.80, "anon-personas"),
    ]
    text = json.dumps(json.loads((CASE_DIR / "case.json").read_text())["deviations"])
    assert "Amendment A2" in text and "20261110" in text and "not human-reviewed" in text


def fake_result(run_no, accuracy=0.90, over=0.02, fill=0.80, agreement=None):
    """A real-dataset result file as the runner writes it, with invented numbers. Only run 3 holds
    run_agreement, as the runner writes it."""
    metrics = {
        "slot_accuracy": accuracy, "over_inferred": over, "first_session_fill": fill,
        "run_agreement": agreement if run_no == 3 else None,
    }  # fmt: skip
    return {
        "run": run_no, "dataset": {"name": "anon-personas", "kind": "real", "sha256": "s", "n": 20},
        "metrics": metrics, "ratios": {}, "invalid": 0,
        "privacy": {"sensitive_reached_model": None, "stated_overwritten": 0, "exclusion_stored": 0},
    }  # fmt: skip


def test_bars_keep_when_haiku_is_good_enough_and_run_3_carries_the_agreement():
    case = load_case(CASE_DIR)
    held = verdict.compute(case, [fake_result(n, agreement=0.9) for n in (1, 2, 3)])
    assert held["verdict"] == "keep" and [b["state"] for b in held["bars"]] == ["holds"] * 5


@pytest.mark.parametrize(
    "kwargs, failing",
    [
        ({"accuracy": 0.82}, [0]),  # mean under 0.85 (min 0.82 still clears 0.80)
        ({"accuracy": 0.78}, [0, 1]),
        ({"over": 0.09}, [2]),
        ({"fill": 0.60}, [3]),
        ({"agreement": 0.70}, [4]),
    ],
)
def test_each_bar_fails_on_its_own(kwargs, failing):
    case = load_case(CASE_DIR)
    kwargs = {"agreement": 0.9, **kwargs}
    out = verdict.compute(case, [fake_result(n, **kwargs) for n in (1, 2, 3)])
    assert out["verdict"] == "drop"
    assert [i for i, b in enumerate(out["bars"]) if b["state"] == "fails"] == failing


def test_no_run_agreement_in_run_3_is_inconclusive():
    case = load_case(CASE_DIR)
    out = verdict.compute(case, [fake_result(n, agreement=None) for n in (1, 2, 3)])
    assert out["verdict"] == "inconclusive" and out["bars"][4]["state"] != "holds"
