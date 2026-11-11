"""Mock of extract/jev: six invented personas (two Hebrew, four English), a scripted stand-in Jev with
scores just under and on each threshold, the exact metric values, the he/en split, the sample, the
Jev guard and the bars. No real text and no Jev or model call."""

import json
import random
import shutil
from pathlib import Path

import pytest

from chat.V2.personalization.lab import results, runner, verdict
from chat.V2.personalization.lab.datasets import Dataset
from chat.V2.personalization.lab.manifest import load_case
from chat.V2.personalization.lab.replay import replay_persona

CASE_DIR = Path(__file__).parent


def case_copy(tmp_path):
    """The case without its results, in a directory outside the repository (with its aspect files)."""
    dest = tmp_path / "extract" / "jev"
    shutil.copytree(CASE_DIR, dest, ignore=shutil.ignore_patterns("results", "__pycache__"))
    for name in ("aspect.json", "metrics.py"):
        shutil.copy(CASE_DIR.parent / name, dest.parent)
    return dest


def module():
    return runner.load_pipeline(CASE_DIR)


def mock_run(tmp_path, items=None, kind="synthetic", typed="fixture", **kw):
    case = load_case(case_copy(tmp_path))
    mod = runner.load_pipeline(case.dir)
    items = items if items is not None else mod.mock_items()
    dataset = Dataset("mock", kind, "sha", items, **kw)
    typed = mod.mock_typed() if typed == "fixture" else typed
    return case, typed, runner.run_case(
        case, kind="mock", run_no=0, dataset=dataset, llm=mod.mock_llm(), typed=typed
    )  # fmt: skip


def replays():
    mod, case = module(), load_case(CASE_DIR)
    typed, llm = mod.mock_typed(), mod.mock_llm()
    out = {}
    for persona in mod.mock_items():
        out[persona["id"]] = replay_persona(mod.build(case.config, llm, typed), persona)
    return out, typed


def profile(replay):
    return {k: v["value"] for k, v in replay.final_profile.items()}


# -- each scripted score on its persona -----------------------------------------------------------


def test_each_persona_ends_with_what_the_scores_around_the_thresholds_allow():
    got = {pid: profile(r) for pid, r in replays()[0].items()}
    assert got == {
        "ava": {"preferred_language": "en", "hebrew_fluency": "none",
                "terminology_comfort": "new", "text_categories": ["Tanakh"]},  # on 0.50 and 0.65: fills
        "ben": {"preferred_language": "he", "hebrew_fluency": "intermediate",
                "terminology_comfort": "comfortable", "text_categories": ["Talmud"]},  # 0.75 replaces
        "cal": {"preferred_language": "en", "hebrew_fluency": "basic"},  # 0.74 does not replace
        "dee": {"preferred_language": "he"},  # 0.49, 0.64 and the 0.50 uncertain band: no fill
        "eve": {"preferred_language": "en", "terminology_comfort": "comfortable"},  # the wrong fill
        "gil": {"preferred_language": "en", "hebrew_fluency": "basic"},  # after one failed request
    }  # fmt: skip


def test_a_choice_just_under_its_threshold_and_a_failed_request_change_nothing():
    reps, _typed = replays()
    ava = [
        s["profile"].get("hebrew_fluency", {}).get("value") for s in reps["ava"].turn_snapshots()
    ]
    assert ava == [None, "none", "none"]  # 0.49 no, 0.50 yes
    ben = [s["profile"]["hebrew_fluency"]["value"] for s in reps["ben"].turn_snapshots()]
    assert ben == ["fluent", "fluent", "intermediate"]  # 0.74 keeps the value, 0.75 replaces it
    assert reps["gil"].diagnostics.get("jev_error") == 1
    assert "hebrew_fluency" not in reps["gil"].turn_snapshots()[0]["profile"]
    assert "text_categories" not in profile(reps["dee"]) and "text_categories" not in profile(
        reps["eve"]
    )


def test_the_sensitive_turn_never_reaches_jev():
    reps, typed = replays()
    assert dict(reps["eve"].skips) == {"sensitive": 1}
    assert not any(
        "mother died" in json.dumps(state, ensure_ascii=False) for state, _q in typed.jev.calls
    )


# -- the request: closed-set questions, the last 12 prompts ----------------------------------------


def test_one_request_per_turn_asks_the_closed_set():
    _reps, typed = replays()
    assert len(typed.jev.calls) == 15  # 16 turns, the sensitive one dropped
    state, questions = typed.jev.calls[0]
    assert set(state) == {"learner_prompts"}
    for key in (
        "hebrew_fluency",
        "terminology_comfort",
        "register",
        "category:Tanakh",
        "category:Talmud",
    ):
        assert key in questions
    assert "unclear" in json.dumps({k: q.to_json() for k, q in questions.items()})


def test_the_state_holds_the_last_12_prompts_only():
    mod, case = module(), load_case(CASE_DIR)
    turns = [
        {"id": f"zed-{i}", "at": f"2026-11-04T09:{i:02d}:00Z", "user": f"Question number {i} about Genesis",
         "assistant": "ok", "tags": []}
        for i in range(1, 15)
    ]  # fmt: skip
    persona = {"id": "zed", "user_id": "mock:zed", "golden": {}, "checks": [], "interview": None,
               "sessions": [{"session_id": "zed-s1", "turns": turns}]}  # fmt: skip
    typed = mod.mock_typed()
    replay_persona(mod.build(case.config, mod.mock_llm(), typed), persona)
    texts = [p["text"] for p in typed.jev.calls[-1][0]["learner_prompts"]]
    assert len(typed.jev.calls) == 14 and len(texts) == 12
    assert (
        texts[0] == "Question number 3 about Genesis"
        and texts[-1] == "Question number 14 about Genesis"
    )


# -- the exact metric values, and the Hebrew / English split ---------------------------------------


def test_exact_metric_values_on_the_fixture(tmp_path):
    _case, _typed, result = mock_run(tmp_path)
    m, r = result["metrics"], result["ratios"]
    # golden slots: ava 4, ben 4, cal 2, dee 3, eve 1, gil 2 = 16; right: 4 + 3 + 2 + 1 + 1 + 2 = 13
    assert r["slot_accuracy"] == [13, 16] and m["slot_accuracy"] == 13 / 16
    assert r["over_inferred"] == [
        1,
        26,
    ]  # eve's terminology_comfort, of 26 empty slots (42 slots less 16 golden)
    assert m["over_inferred"] == 1 / 26
    # ava, ben, cal and gil end session 1 with a correct non-language slot; dee has none, eve's is wrong
    assert r["first_session_fill"] == [4, 6]
    assert m["slot_accuracy_on@language"] == 1.0
    assert m["slot_accuracy_on@text_categories"] == 2 / 3  # ava and ben; dee's scores stayed under
    assert m["over_inferred_on@text_categories"] == 0.0
    assert m["sensitive_dropped_share"] == 1 / 16
    assert result["dataset"]["n"] == 6 and result["invalid"] == 0


def test_accuracy_is_reported_separately_for_hebrew_and_english_prompts(tmp_path):
    _case, _typed, result = mock_run(tmp_path)
    m, r = result["metrics"], result["ratios"]
    assert r["slot_accuracy_by_lang@he"] == [4, 7]  # ben 3 of 4, dee 1 of 3
    assert m["slot_accuracy_by_lang@he"] == 4 / 7
    assert r["slot_accuracy_by_lang@en"] == [9, 9] and m["slot_accuracy_by_lang@en"] == 1.0
    assert m["over_inferred_by_lang@he"] == 0.0
    assert (
        m["over_inferred_by_lang@en"] == 1 / 19
    )  # eve's fill, of 19 empty slots among four personas


def test_typed_requests_are_the_jev_requests_that_answered(tmp_path):
    _case, typed, result = mock_run(tmp_path)
    assert len(typed.jev.calls) == 15  # one failed: it is a request but not a ledger row
    assert result["cost"]["typed_requests"] == 14
    assert result["cost"]["calls"] == 20  # 14 typed + the nightly batch once per persona
    assert result["privacy"]["sensitive_reached_model"] == 0
    assert result["privacy"]["stated_overwritten"] == 0


# -- the Jev guard: the case never weakens it --------------------------------------------------------


def test_jev_is_refused_on_a_real_dataset_that_is_not_anonymized(tmp_path):
    with pytest.raises(runner.JevOnIdentifiableData):
        mock_run(tmp_path, kind="real", anonymized=False)


def test_a_run_without_a_jev_client_is_invalid_not_a_guess(tmp_path):
    _case, _typed, result = mock_run(tmp_path, typed=None)
    assert result["invalid"] == 6 and result["metrics"]["slot_accuracy"] is None


# -- the A2 seeded sample -----------------------------------------------------------------------------


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
    _case, _typed, result = mock_run(tmp_path, kind="real", anonymized=True)
    results.check_real(result)  # raises on per-item rows or free text
    assert "per_item" not in result


# -- the manifest as registered -----------------------------------------------------------------------


def test_manifest_has_the_rows_bars_on_anon_personas_and_the_deviations():
    case = load_case(CASE_DIR)
    assert case.status == "planned" and case.engine == "offline" and case.task == "replay"
    assert case.datasets == ["syn1", "anon-personas"]
    assert case.config["strategy"] == "hybrid_jev" and case.config["typed"] == "jev"
    got = [(b.metric, b.agg, b.op, b.value, b.dataset) for b in case.keep_if]
    assert got == [  # the same accuracy bars as extract/haiku-typed
        ("slot_accuracy", "mean", ">=", 0.85, "anon-personas"),
        ("slot_accuracy", "min", ">=", 0.80, "anon-personas"),
        ("over_inferred", "mean", "<=", 0.05, "anon-personas"),
        ("first_session_fill", "mean", ">=", 0.75, "anon-personas"),
    ]
    for name in ("slot_accuracy_by_lang@he", "slot_accuracy_by_lang@en"):
        assert name in case.metrics and all(
            b.metric != name for b in case.keep_if
        )  # reported, no bar
    text = json.dumps(json.loads((CASE_DIR / "case.json").read_text())["deviations"])
    assert "Amendment A2" in text and "decision 3" in text and "20261110" in text
    assert "not human-reviewed" in text


def fake_result(run_no, accuracy=0.90, over=0.02, fill=0.80):
    """A real-dataset result file as the runner writes it, with invented numbers."""
    metrics = {"slot_accuracy": accuracy, "over_inferred": over, "first_session_fill": fill}
    return {
        "run": run_no, "dataset": {"name": "anon-personas", "kind": "real", "sha256": "s", "n": 20},
        "metrics": metrics, "ratios": {}, "invalid": 0,
        "privacy": {"sensitive_reached_model": None, "stated_overwritten": 0, "exclusion_stored": 0},
    }  # fmt: skip


def test_bars_keep_when_jev_is_good_enough():
    case = load_case(CASE_DIR)
    held = verdict.compute(case, [fake_result(n) for n in (1, 2, 3)])
    assert held["verdict"] == "keep" and [b["state"] for b in held["bars"]] == ["holds"] * 4


@pytest.mark.parametrize(
    "kwargs, failing",
    [
        ({"accuracy": 0.82}, [0]),  # mean under 0.85 (min 0.82 still clears 0.80)
        ({"accuracy": 0.78}, [0, 1]),
        ({"over": 0.09}, [2]),
        ({"fill": 0.60}, [3]),
    ],
)
def test_each_bar_fails_on_its_own(kwargs, failing):
    case = load_case(CASE_DIR)
    out = verdict.compute(case, [fake_result(n, **kwargs) for n in (1, 2, 3)])
    assert out["verdict"] == "drop"
    assert [i for i, b in enumerate(out["bars"]) if b["state"] == "fails"] == failing
