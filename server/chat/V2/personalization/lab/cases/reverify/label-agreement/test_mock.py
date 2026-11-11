"""Mock of reverify/label-agreement: a fake labeller that differs from the frozen golden in exactly
k of n slots gives label_agreement (n-k)/n. Invented personas, no real text, no model call."""

import json
import random
import shutil
from pathlib import Path

from chat.V2.personalization.lab import results, runner, validate_data, verdict
from chat.V2.personalization.lab.datasets import Dataset
from chat.V2.personalization.lab.manifest import load_case
from chat.V2.personalization.llm import FakeLLMClient

CASE_DIR = Path(__file__).parent
CASE_ID = "reverify/label-agreement"
SLOTS = [
    "preferred_language", "hebrew_fluency", "terminology_comfort", "register",
    "text_categories", "answer_style", "learning_habits",
]  # fmt: skip


def case_copy(tmp_path):
    """The case without its results, in a directory outside the repository."""
    dest = tmp_path / "reverify" / "label-agreement"
    shutil.copytree(CASE_DIR, dest, ignore=shutil.ignore_patterns("results", "__pycache__"))
    shutil.copy(CASE_DIR.parent / "aspect.json", dest.parent)
    shutil.copy(CASE_DIR.parent / "metrics.py", dest.parent)
    return dest


def mock_run(tmp_path, dataset_name="mock", kind="synthetic", llm=None, **kw):
    case = load_case(case_copy(tmp_path))
    mod = runner.load_pipeline(case.dir)
    dataset = Dataset(dataset_name, kind, "sha", mod.mock_items(), **kw)
    return case, runner.run_case(
        case, kind="mock", run_no=0, dataset=dataset, llm=llm or mod.mock_llm()
    )


# -- the exact values ---------------------------------------------------------------------------
# persona A: 6 slots filled, the fake differs on 2 (hebrew_fluency, register) -> 4 of 6
# persona B: 4 slots filled by either, the fake differs on 2 (terminology_comfort, and
#            answer_style that only the fake filled) -> 2 of 4;  pooled 6 of 10 = (10 - 4) / 10


def test_a_labeller_that_differs_in_k_of_n_slots_gives_n_minus_k_over_n(tmp_path):
    _case, result = mock_run(tmp_path)
    m = result["metrics"]
    assert result["ratios"]["label_agreement"] == [6, 10]
    assert m["label_agreement"] == (10 - 4) / 10
    assert result["invalid"] == 0 and result["dataset"]["n"] == 2


def test_per_slot_agreement_and_both_empty_share(tmp_path):
    _case, result = mock_run(tmp_path)
    m = result["metrics"]
    assert {s: m[f"label_agreement@{s}"] for s in SLOTS} == {
        "preferred_language": 1.0,
        "hebrew_fluency": 0.0,  # persona A differs; persona B is empty in both: only A counts
        "terminology_comfort": 0.5,
        "register": 0.0,
        "text_categories": 1.0,
        "answer_style": 0.5,  # persona A shares a keyword; persona B only the fake filled it
        "learning_habits": None,  # empty in both labellers everywhere
    }
    assert result["ratios"]["label_both_empty_share"] == [4, 14]
    assert m["label_both_empty_share"] == 4 / 14


def test_per_item_rows_of_a_synthetic_dataset_hold_letters_not_labels(tmp_path):
    _case, result = mock_run(tmp_path)
    assert [row["predicted"] for row in result["per_item"]] == ["ADADAAB", "ABDBAOB"]
    text = json.dumps(result["per_item"])
    assert "academic" not in text and "intermediate" not in text


def test_the_labeller_sees_the_turns_and_no_labels(tmp_path):
    seen = []
    mod = runner.load_pipeline(CASE_DIR)
    inner = mod.mock_llm()

    def spy(system, user):
        seen.append((system, user))
        return (
            inner.respond(system, user)
            if hasattr(inner, "respond")
            else inner.complete(system=system, user=user, model="m").text
        )

    mock_run(tmp_path, llm=FakeLLMClient(spy))
    assert len(seen) == 2
    for system, user in seen:
        assert "slot set v2" in system.lower() or "slot set" in system.lower()
        for banned in ("golden", "opus_golden", "description", "tags", "mock-a"):
            assert banned not in user
        assert set(json.loads(user)) == {"user_id", "sessions"}


def test_it_uses_the_labs_own_labeling_prompt():
    from chat.V2.personalization.lab import label

    mod = runner.load_pipeline(CASE_DIR)
    assert mod.BRIEF == "anon-personas" and label.build_system(mod.BRIEF)
    assert not hasattr(mod, "SYSTEM")  # no copy of the prompt text in the case


def test_a_reply_with_no_valid_labels_is_invalid_and_left_out(tmp_path):
    _case, result = mock_run(tmp_path, llm=FakeLLMClient(lambda system, user: "not json"))
    assert result["invalid"] == 2
    assert result["metrics"]["label_agreement"] is None
    assert [r["predicted"] for r in result["per_item"]] == ["invalid", "invalid"]


# -- the real dataset carries numbers only; the sample is fixed -----------------------------------


def test_a_run_on_anon_personas_carries_numbers_only(tmp_path, monkeypatch):
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(tmp_path / "la-lab-data"))
    _case, result = mock_run(tmp_path, "anon-personas", "real", anonymized=True)
    results.check_real(result, {"label_agreement", "label_both_empty_share"})
    assert "per_item" not in result
    assert result["metrics"]["label_agreement"] == 0.6


def test_the_sample_is_20_personas_fixed_by_the_seed_in_case_json():
    case = load_case(CASE_DIR)
    config = case.config
    assert (config["sample_size"], config["sample_dataset"], config["sample_seed"]) == (
        20, "anon-personas", 20261110,
    )  # fmt: skip
    items = [{"user_id": f"mock:{i:03d}", "sessions": []} for i in range(30)]
    real = Dataset("anon-personas", "real", "sha", items, anonymized=True)
    picked = runner.load_pipeline(CASE_DIR).select_items(items, config, real)
    ids = sorted(i["user_id"] for i in items)
    assert [p["persona"]["user_id"] for p in picked] == sorted(
        random.Random(config["sample_seed"]).sample(ids, 20)
    )
    syn1 = Dataset("syn1", "synthetic", "sha", items)
    assert len(runner.load_pipeline(CASE_DIR).select_items(items, config, syn1)) == 30


# -- the live run's resume file -------------------------------------------------------------------


def test_a_finished_item_is_read_back_from_the_partial_file_not_asked_again(tmp_path, monkeypatch):
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(tmp_path / "la-lab-data"))
    case = load_case(case_copy(tmp_path))
    mod = runner.load_pipeline(case.dir)
    calls = []

    def respond(system, user):
        calls.append(user)
        return mod.mock_llm().complete(system=system, user=user, model="m").text

    item = mod.select_items(mod.mock_items(), case.config, Dataset("syn1", "synthetic", "sha", []))[
        0
    ]
    item["dataset"] = "syn1"
    first = mod.predict(item, case.config, FakeLLMClient(respond))
    again = mod.predict(item, case.config, FakeLLMClient(respond))
    assert first == again == "ADADAAB" and len(calls) == 1
    assert next((tmp_path / "la-lab-data" / "partial").rglob("opus_syn1_run1.json")).is_file()


# -- the manifest as registered -------------------------------------------------------------------


def test_manifest_has_the_bar_on_anon_personas_and_the_deviation():
    case = load_case(CASE_DIR)
    assert case.datasets == ["syn1", "anon-personas"] and case.task == "label"
    assert case.config["model"] == "claude-opus-5-5"
    assert [(b.metric, b.agg, b.op, b.value, b.dataset) for b in case.keep_if] == [
        ("label_agreement", "mean", ">=", 0.85, "anon-personas")
    ]
    assert set(case.metrics) == {
        "label_agreement",
        "label_both_empty_share",
        *[f"label_agreement@{s}" for s in SLOTS],
    }
    text = json.dumps(json.loads((CASE_DIR / "case.json").read_text())["deviations"])
    assert "not human-reviewed" in text and "A1" in text and "A2" in text and "A3" in text


def fake_result(value):
    return {
        "dataset": {"name": "anon-personas", "kind": "real", "sha256": "s", "n": 20},
        "metrics": {"label_agreement": value}, "ratios": {}, "invalid": 0,
        "privacy": {"sensitive_reached_model": None, "stated_overwritten": 0, "exclusion_stored": 0},
    }  # fmt: skip


def test_the_bar_keeps_at_085_and_drops_below():
    case = load_case(CASE_DIR)
    assert verdict.compute(case, [fake_result(v) for v in (0.86, 0.87, 0.85)])["verdict"] == "keep"
    assert verdict.compute(case, [fake_result(v) for v in (0.7, 0.72, 0.71)])["verdict"] == "drop"


def test_the_slot_list_is_the_registry():
    assert SLOTS == list(validate_data.load_slots())
