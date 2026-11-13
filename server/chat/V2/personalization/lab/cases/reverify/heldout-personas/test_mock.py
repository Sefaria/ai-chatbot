"""Mock of reverify/heldout-personas: the scorer, the five designs on two invented personas, the
fixed sample, the bars and the explain line. No real text and no model call."""

import json
import random
import shutil
from pathlib import Path

from chat.V2.personalization.lab import __main__ as cli
from chat.V2.personalization.lab import results, runner, verdict
from chat.V2.personalization.lab.datasets import Dataset
from chat.V2.personalization.lab.manifest import load_case
from chat.V2.personalization.lab.metrics import RunData, over_inferred, slot_accuracy
from chat.V2.personalization.lab.replay import PersonaReplay, golden_value

CASE_DIR = Path(__file__).parent
DESIGNS = ["realtime", "nightly", "hybrid", "explicit", "hybrid_jev"]
CASE_ID = "reverify/heldout-personas"


def case_copy(tmp_path):
    """The case without its results, in a directory outside the repository."""
    dest = tmp_path / "reverify" / "heldout-personas"
    shutil.copytree(CASE_DIR, dest, ignore=shutil.ignore_patterns("results", "__pycache__"))
    shutil.copy(CASE_DIR.parent / "aspect.json", dest.parent)
    return dest


def module():
    return runner.load_pipeline(CASE_DIR)


def mock_run(tmp_path, typed="fixture", **kw):
    case = load_case(case_copy(tmp_path))
    mod = runner.load_pipeline(case.dir)
    items = mod.mock_items()
    dataset = Dataset("mock", "synthetic", "sha", items, **kw)
    typed = mod.mock_typed() if typed == "fixture" else typed
    return case, runner.run_case(
        case, kind="mock", run_no=0, dataset=dataset, llm=mod.mock_llm(), typed=typed
    )


# -- the scorer audit ---------------------------------------------------------------------------


def replays(profile_of):
    out = []
    for persona in module().mock_items():
        golden = {slot: golden_value(g) for slot, g in persona["golden"].items() if g}
        out.append(PersonaReplay(persona, final_profile=profile_of(golden)))
    return out


def as_slots(golden):
    return {slot: {"value": value} for slot, value in golden.items()}


def test_golden_profile_in_gives_slot_accuracy_one():
    data = RunData(replays=replays(as_slots))
    assert slot_accuracy(data).value == 1.0


def test_empty_profile_in_gives_no_fills_and_zero_accuracy():
    data = RunData(replays=replays(lambda golden: {}))
    assert slot_accuracy(data).value == 0.0
    assert over_inferred(data).num == 0  # nothing was filled


def test_one_planted_wrong_slot_per_persona_gives_exactly_n_minus_one_over_n():
    def plant(golden):
        slots = as_slots(golden)
        slots["preferred_language"] = {"value": "mixed"}  # wrong for both personas
        return slots

    data = RunData(replays=replays(plant))
    n = 4  # scored slots per fixture persona
    ratio = slot_accuracy(data)
    assert (ratio.num, ratio.den) == (2 * (n - 1), 2 * n)
    assert ratio.value == (n - 1) / n


# -- each design on the two-persona fixture, with a scripted fake -------------------------------


def test_each_design_on_the_two_persona_fixture(tmp_path):
    # the fake model answers from each persona's golden profile, so what a design misses is the
    # design's own rule (the nightly job needs more than one session; explicit asks no model)
    _case, result = mock_run(tmp_path)
    metrics = result["metrics"]
    assert {d: metrics[f"slot_accuracy@{d}"] for d in DESIGNS} == {
        "realtime": 1.0, "nightly": 0.5, "hybrid": 0.5, "explicit": 0.0, "hybrid_jev": 1.0,
    }  # fmt: skip
    assert {d: metrics[f"checks_passed@{d}"] for d in DESIGNS} == {
        "realtime": 1.0, "nightly": 0.0, "hybrid": 1.0, "explicit": 0.0, "hybrid_jev": 1.0,
    }  # fmt: skip
    assert result["dataset"]["n"] == 2 and result["invalid"] == 0
    assert result["cost"]["typed_requests"] == 6  # Jev: 3 turns x 2 personas, design E only
    assert result["privacy"]["sensitive_reached_model"] == 0


def test_design_e_without_jev_is_invalid_and_the_verdict_is_inconclusive(tmp_path):
    case, result = mock_run(tmp_path, typed=None)
    assert result["metrics"]["slot_accuracy@hybrid_jev"] is None
    assert result["metrics"]["slot_accuracy@realtime"] == 1.0
    assert result["invalid"] == 2  # both personas, for design E
    runs = [fake_result(run, e_accuracy=None) for run in (1, 2, 3)]
    assert verdict.compute(case, runs)["verdict"] == "inconclusive"


# -- the fixed sample ---------------------------------------------------------------------------


def invented_personas(n=30):
    return [{"user_id": f"mock:{i:03d}", "sessions": []} for i in range(n)]


def test_the_sample_is_20_personas_fixed_by_the_seed_in_case_json():
    case = load_case(CASE_DIR)
    config = case.config
    assert (config["sample_size"], config["sample_dataset"]) == (20, "anon-personas")
    items = invented_personas()
    real = Dataset("anon-personas", "real", "sha", items, anonymized=True)
    picked = module().select_items(items, config, real)
    ids = sorted(i["user_id"] for i in items)
    assert [p["user_id"] for p in picked] == sorted(
        random.Random(config["sample_seed"]).sample(ids, 20)
    )
    shuffled = list(reversed(items))
    assert (
        module().select_items(shuffled, config, real) == picked
    )  # order of the pull does not matter


def test_syn1_and_smaller_datasets_are_not_sampled():
    config = load_case(CASE_DIR).config
    items = invented_personas()
    syn1 = Dataset("syn1", "synthetic", "sha", items)
    assert len(module().select_items(items, config, syn1)) == 30
    small = Dataset("anon-personas", "real", "sha", items[:7], anonymized=True)
    assert len(module().select_items(items[:7], config, small)) == 7


# -- the manifest as registered ------------------------------------------------------------------


def test_manifest_has_the_rows_bars_on_anon_personas_and_the_deviation():
    case = load_case(CASE_DIR)
    assert case.datasets == ["syn1", "anon-personas"]
    assert case.config["variants"] == DESIGNS
    got = [(b.metric, b.agg, b.op, b.value, b.dataset) for b in case.keep_if]
    assert got == [
        ("slot_accuracy@hybrid_jev", "mean", ">=", 0.91, "anon-personas"),
        ("slot_accuracy@hybrid_jev", "min", ">=", 0.88, "anon-personas"),
        ("lead_over_next@hybrid_jev", "min", ">", 0, "anon-personas"),
        ("checks_passed@hybrid_jev", "mean", ">=", 0.90, "anon-personas"),
    ]
    text = json.dumps(json.loads((CASE_DIR / "case.json").read_text())["deviations"])
    assert "188" in text and "14x" in text and "sample" in text


def fake_result(run_no, e_accuracy=0.95, e_checks=0.95, other=0.80, lead=None):
    """A real-dataset result file as the runner writes it, with invented numbers."""
    metrics = {f"slot_accuracy@{d}": other for d in DESIGNS}
    metrics.update({f"checks_passed@{d}": 0.5 for d in DESIGNS})
    metrics["slot_accuracy@hybrid_jev"] = e_accuracy
    metrics["checks_passed@hybrid_jev"] = e_checks
    metrics["lead_over_next@hybrid_jev"] = (
        None if e_accuracy is None else (e_accuracy if lead is None else lead) - other
    )
    return {
        "dataset": {"name": "anon-personas", "kind": "real", "sha256": "s", "n": 20},
        "metrics": metrics, "ratios": {}, "invalid": 0 if e_accuracy is not None else 20,
        "privacy": {"sensitive_reached_model": None, "stated_overwritten": 0, "exclusion_stored": 0},
    }  # fmt: skip


def test_bars_keep_when_the_headline_holds_and_drop_when_it_does_not():
    case = load_case(CASE_DIR)
    held = verdict.compute(case, [fake_result(n) for n in (1, 2, 3)])
    assert held["verdict"] == "keep" and [b["state"] for b in held["bars"]] == ["holds"] * 4
    fell = verdict.compute(
        case, [fake_result(n, e_accuracy=0.85, e_checks=0.85) for n in (1, 2, 3)]
    )
    assert fell["verdict"] == "drop"
    assert [b["state"] for b in fell["bars"]] == [
        "fails",
        "fails",
        "holds",
        "fails",
    ]  # lead is 0.05


def test_a_run_on_anon_personas_carries_numbers_only(tmp_path, monkeypatch):
    monkeypatch.setenv("LA_LAB_DATA_DIR", str(tmp_path / "la-lab-data"))
    case = load_case(case_copy(tmp_path))
    mod = runner.load_pipeline(case.dir)
    real = Dataset("anon-personas", "real", "sha", mod.mock_items(), anonymized=True)
    result = runner.run_case(
        case, kind="mock", run_no=0, dataset=real, llm=mod.mock_llm(), typed=mod.mock_typed()
    )
    results.check_real(result)  # raises on per-item rows or free text
    assert "per_item" not in result


# -- explain ------------------------------------------------------------------------------------


def test_explain_shows_iter1_syn1_and_syn2_per_design(tmp_path, capsys):
    directory = case_copy(tmp_path)
    syn1 = {"a": 0.50, "b": 0.60, "c": 0.70, "d": 0.10, "e": 0.90}
    anon = {"a": 0.40, "b": 0.55, "c": 0.65, "d": 0.20, "e": 0.8123}
    for run_no in (1, 2, 3):
        for name, table in ((f"run{run_no}.json", syn1), (f"real_run{run_no}.json", anon)):
            metrics = {
                f"slot_accuracy@{d}": table[k] for d, k in zip(DESIGNS, "abcde", strict=True)
            }
            results_dir = directory / "results"
            results_dir.mkdir(exist_ok=True)
            (results_dir / name).write_text(
                json.dumps({"dataset": {"name": "x"}, "metrics": metrics}), encoding="utf-8"
            )
    assert cli.main(["--cases-root", str(tmp_path), "explain", CASE_ID]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "  realtime: iter1 0.80 | syn1 0.50 | syn2 0.40" in lines
    assert "  nightly: iter1 0.86 | syn1 0.60 | syn2 0.55" in lines
    assert "  hybrid: iter1 0.86 | syn1 0.70 | syn2 0.65" in lines
    assert "  explicit: iter1 0.44 | syn1 0.10 | syn2 0.20" in lines
    assert "  hybrid_jev: iter1 0.96 | syn1 0.90 | syn2 0.81" in lines  # one line per design


def test_explain_says_n_a_for_a_design_that_has_no_figure(tmp_path, capsys):
    directory = case_copy(tmp_path)
    (directory / "results").mkdir()
    (directory / "results" / "real_run1.json").write_text(
        json.dumps(
            {
                "dataset": {"name": "anon-personas"},
                "metrics": {"slot_accuracy@hybrid_jev": None, "slot_accuracy@realtime": 0.7},
            }
        ),
        encoding="utf-8",
    )
    assert cli.main(["--cases-root", str(tmp_path), "explain", CASE_ID]) == 0
    out = capsys.readouterr().out
    assert "hybrid_jev: iter1 0.96 | syn1 n/a | syn2 n/a" in out
    assert "realtime: iter1 0.80 | syn1 n/a | syn2 0.70" in out
