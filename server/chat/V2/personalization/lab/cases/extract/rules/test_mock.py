"""Mock of extract/rules: each rule on invented personas, the exact metric values, no model call.
No real text. The fixture's eight personas each probe one rule (see `trap` in mock_fixture.json)."""

import json
import shutil
from pathlib import Path

import pytest

from chat.V2.personalization.lab import results, runner, verdict
from chat.V2.personalization.lab.datasets import Dataset
from chat.V2.personalization.lab.manifest import load_case
from chat.V2.personalization.lab.metrics import METRICS, RunData, evaluate
from chat.V2.personalization.lab.replay import replay_persona
from chat.V2.personalization.llm import FakeLLMClient

CASE_DIR = Path(__file__).parent
CASE_ID = "extract/rules"


def case_copy(tmp_path):
    """The case without its results, in a directory outside the repository (with its aspect files)."""
    dest = tmp_path / "extract" / "rules"
    shutil.copytree(CASE_DIR, dest, ignore=shutil.ignore_patterns("results", "__pycache__"))
    for name in ("aspect.json", "metrics.py"):
        shutil.copy(CASE_DIR.parent / name, dest.parent)
    return dest


def module():
    return runner.load_pipeline(CASE_DIR)


def exploding_llm():
    def respond(system, user):
        raise AssertionError("extract/rules must not call a model")

    return FakeLLMClient(respond)


def mock_run(tmp_path, items=None, kind="synthetic", **kw):
    case = load_case(case_copy(tmp_path))
    mod = runner.load_pipeline(case.dir)
    items = items if items is not None else mod.mock_items()
    dataset = Dataset("mock", kind, "sha", items, **kw)
    return case, runner.run_case(
        case, kind="mock", run_no=0, dataset=dataset, llm=exploding_llm(), typed=None
    )


# -- each rule on its persona ---------------------------------------------------------------------


def final_profiles():
    mod = module()
    out = {}
    for persona in mod.mock_items():
        replay = replay_persona(mod.build({}), persona)
        out[persona["id"]] = ({k: v["value"] for k, v in replay.final_profile.items()}, replay)
    return out


def test_each_rule_on_its_persona():
    got = {pid: profile for pid, (profile, _r) in final_profiles().items()}
    assert got == {
        "ana": {"preferred_language": "en", "text_categories": ["Talmud"]},  # Rashi named once: no fill
        "dov": {"preferred_language": "he", "text_categories": ["Mishnah"]},  # Zohar named once
        "eli": {"preferred_language": "en"},  # one mention is not a recurring interest
        "fay": {"preferred_language": "en"},  # the sensitive turn does not count
        "gil": {"preferred_language": "he", "text_categories": ["Tanakh"]},  # en, then two he sessions
        "hana": {"preferred_language": "en"},  # an instruction turn is not interest
        "ivy": {},  # no letters, no language
        "jon": {"preferred_language": "en"},  # Halakhah named once
    }  # fmt: skip


def test_the_screens_drop_turns_before_they_count():
    replays = {pid: r for pid, (_p, r) in final_profiles().items()}
    assert dict(replays["fay"].skips) == {"sensitive": 1}
    assert dict(replays["hana"].skips) == {"instruction": 1}
    assert sum(r.turns for r in replays.values()) == 22


def test_the_rules_only_fill_language_and_text_categories():
    for profile, _r in final_profiles().values():
        assert set(profile) <= {"preferred_language", "text_categories"}


def test_at_most_three_categories_most_named_first():
    turns = [
        "Midrash Rabbah on Genesis", "What is Midrash?", "Midrash for kids",  # Midrash x3 (and Tanakh)
        "Talmud study tips", "Talmud page one", "Mishnah and Talmud",  # Talmud x3
        "Zohar basics", "Zohar and Kabbalah",  # Kabbalah x2
        "Tanya for beginners", "A Tanya class",  # Chasidut x2, the fourth category
    ]  # fmt: skip
    persona = {
        "id": "x", "user_id": "mock:x", "golden": {}, "checks": [], "interview": None,
        "sessions": [{"session_id": "s", "turns": [
            {"id": f"x-{i}", "at": f"2026-11-04T09:{i:02d}:00Z", "user": t, "assistant": "ok", "tags": []}
            for i, t in enumerate(turns)
        ]}],
    }  # fmt: skip
    replay = replay_persona(module().build({}), persona)
    assert replay.final_profile["text_categories"]["value"] == ["Midrash", "Talmud", "Kabbalah"]


# -- the exact metric values on the fixture --------------------------------------------------------


def test_exact_metric_values_on_the_fixture(tmp_path):
    _case, result = mock_run(tmp_path)
    m = result["metrics"]
    assert m["slot_accuracy_on@language"] == 1.0  # 7 of 7 personas that have a golden language
    assert m["over_inferred_on@language"] == 0.0  # ivy: golden empty, profile empty
    assert (
        m["slot_accuracy_on@text_categories"] == 0.75
    )  # ana, dov, gil right; jon (one mention) missed
    assert m["over_inferred_on@text_categories"] == 0.0  # eli, fay, hana, ivy stay empty
    assert m["slot_accuracy"] == 10 / 11 and m["over_inferred"] == 0.0
    assert m["calls_per_turn"] == 0.0 and m["usd_per_1000_turns"] == 0.0
    assert m["sensitive_dropped_share"] == 1 / 22
    assert result["ratios"]["slot_accuracy_on@text_categories"] == [3, 4]
    assert result["ratios"]["over_inferred_on@language"] == [0, 1]
    assert result["ratios"]["over_inferred_on@text_categories"] == [0, 4]
    assert result["dataset"]["n"] == 8 and result["invalid"] == 0


def test_no_model_is_called_and_nothing_is_sent(tmp_path):
    _case, result = mock_run(tmp_path)  # the stand-in model raises if it is ever called
    assert result["cost"]["calls"] == 0 and result["cost"]["typed_requests"] == 0
    assert result["cost"]["usd"] == 0
    assert result["privacy"]["sensitive_reached_model"] == 0
    assert module().build({}).sent_texts() == [] and module().build({}).calls() == []


def test_fill_rate_at_k_on_the_fixture(tmp_path):
    _case, result = mock_run(tmp_path)
    m = result["metrics"]
    # every persona has 2 or more turns, only gil (6 turns) has 4 or more, none has 8
    assert m["fill_rate_at_k@4"] == 2 / 2  # gil: language and text_categories both filled by turn 4
    # 9 of 11 slots: gil and jon have no text category yet after two turns
    assert m["fill_rate_at_k@2"] == 9 / 11 and m["fill_rate_at_k@8"] is None


# -- the run is deterministic ---------------------------------------------------------------------


def test_two_runs_give_equal_metrics_and_runs_identical_is_one(tmp_path):
    _case, first = mock_run(tmp_path / "a")
    _case, second = mock_run(tmp_path / "b")
    assert first["metrics"] == second["metrics"]
    data = RunData(prior_metrics=[first["metrics"]], metrics_so_far=second["metrics"])
    assert evaluate(METRICS["runs_identical"], data)[0] == 1.0
    drifted = {**second["metrics"], "slot_accuracy_on@language": 0.5}
    data = RunData(prior_metrics=[first["metrics"]], metrics_so_far=drifted)
    assert evaluate(METRICS["runs_identical"], data)[0] == 0.0


def test_run_agreement_is_one_when_the_profiles_do_not_change(tmp_path):
    case, result = mock_run(tmp_path)
    mod = runner.load_pipeline(case.dir)
    replays = [replay_persona(mod.build({}), p) for p in mod.mock_items()]
    same = {
        r.persona["user_id"]: {k: v["value"] for k, v in r.final_profile.items()} for r in replays
    }
    data = RunData(replays=replays, prior_profiles=[same, same])
    assert evaluate(METRICS["run_agreement"], data)[0] == 1.0


# -- all of anon-personas, no sample ---------------------------------------------------------------


def test_every_persona_of_a_larger_dataset_is_run_with_no_sample(tmp_path):
    items = [
        dict(p, user_id=f"mock:{i:03d}-{p['id']}") for i in range(30) for p in module().mock_items()
    ]
    assert not hasattr(module(), "select_items") and "sample_size" not in load_case(CASE_DIR).config
    _case, result = mock_run(tmp_path, items=items)
    assert result["dataset"]["n"] == 240  # 188 in the real dataset: all of them, not 20


def test_a_run_on_anon_personas_carries_numbers_only(tmp_path, monkeypatch):
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(tmp_path / "la-lab-data"))
    _case, result = mock_run(tmp_path, kind="real", anonymized=True)
    results.check_real(result)  # raises on per-item rows or free text
    assert "per_item" not in result


# -- the manifest as registered ---------------------------------------------------------------------


def test_manifest_has_the_rows_bars_on_anon_personas_and_the_deviation():
    case = load_case(CASE_DIR)
    assert case.status == "planned" and case.engine == "offline" and case.task == "replay"
    assert case.datasets == ["syn1", "anon-personas"]
    got = [(b.metric, b.agg, b.op, b.value, b.dataset) for b in case.keep_if]
    assert got == [
        ("slot_accuracy_on@language", "mean", ">=", 0.90, "anon-personas"),
        ("slot_accuracy_on@text_categories", "mean", ">=", 0.90, "anon-personas"),
        ("over_inferred_on@language", "mean", "<=", 0.02, "anon-personas"),
        ("over_inferred_on@text_categories", "mean", "<=", 0.02, "anon-personas"),
        ("calls_per_turn", "mean", "==", 0, "anon-personas"),
        ("runs_identical", "mean", "==", 1, "anon-personas"),
    ]
    text = json.dumps(json.loads((CASE_DIR / "case.json").read_text())["deviations"])
    assert "Amendment A2" in text and "188" in text and "not human-reviewed" in text


def fake_result(run_no, language=1.0, text=0.95):
    """A real-dataset result file as the runner writes it, with invented numbers."""
    metrics = {
        "slot_accuracy_on@language": language, "slot_accuracy_on@text_categories": text,
        "over_inferred_on@language": 0.0, "over_inferred_on@text_categories": 0.01,
        "calls_per_turn": 0.0, "runs_identical": 1.0,
    }  # fmt: skip
    return {
        "dataset": {"name": "anon-personas", "kind": "real", "sha256": "s", "n": 188},
        "metrics": metrics, "ratios": {}, "invalid": 0,
        "privacy": {"sensitive_reached_model": None, "stated_overwritten": 0, "exclusion_stored": 0},
    }  # fmt: skip


def test_bars_keep_when_code_alone_is_enough_and_drop_when_text_categories_fall_short():
    case = load_case(CASE_DIR)
    held = verdict.compute(case, [fake_result(n) for n in (1, 2, 3)])
    assert held["verdict"] == "keep" and [b["state"] for b in held["bars"]] == ["holds"] * 6
    fell = verdict.compute(case, [fake_result(n, text=0.80) for n in (1, 2, 3)])
    assert fell["verdict"] == "drop"
    assert [b["state"] for b in fell["bars"]][:2] == ["holds", "fails"]


@pytest.mark.parametrize("key", ["slot_accuracy_on", "over_inferred_on"])
def test_the_groups_the_bars_name_exist(key):
    assert set(METRICS[key].params) == {"language", "text_categories"}
