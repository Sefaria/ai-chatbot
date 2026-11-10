"""Mock of reverify/cost-recount: a fixed fixture of per-call rows with known token counts gives a
known cost per design at both price tables; the overhead is subtracted once per call; the 10
empty calls per run are the only model calls. No real text and no real model call."""

import copy
import json
import shutil
from pathlib import Path

import pytest

from chat.V2.personalization.lab import __main__ as cli
from chat.V2.personalization.lab import runner, verdict
from chat.V2.personalization.lab.datasets import Dataset
from chat.V2.personalization.lab.manifest import load_case
from chat.V2.personalization.llm import ClaudeCodeLLMClient

CASE_DIR = Path(__file__).parent
CASE_ID = "reverify/cost-recount"
DESIGNS = ["realtime", "nightly", "hybrid", "explicit", "hybrid_jev"]
FIXTURE = json.loads((CASE_DIR / "mock_fixture.json").read_text(encoding="utf-8"))
# the fixture's turns: ava 3, ben 2. Costs at Haiku 4.5 ($1 / $5 per M) and Jev ($0.042 per M in):
USD_45 = {"realtime": 1.5, "nightly": 0.9, "hybrid": 1.05, "explicit": 0.0, "hybrid_jev": 0.792}
# the same rows at Haiku 5.5 ($0.10 / $0.50): Haiku rows cost a tenth, Jev rows the same
USD_55 = {"realtime": 0.15, "nightly": 0.09, "hybrid": 0.105, "explicit": 0.0, "hybrid_jev": 0.117}


def case_copy(tmp_path):
    """The case without its results, in a directory outside the repository."""
    dest = tmp_path / "reverify" / "cost-recount"
    shutil.copytree(CASE_DIR, dest, ignore=shutil.ignore_patterns("results", "__pycache__"))
    for name in ("aspect.json", "metrics.py"):
        shutil.copy(CASE_DIR.parent / name, dest.parent)
    return dest


def mock_run(tmp_path, fixture=None):
    case = load_case(case_copy(tmp_path))
    mod = runner.load_pipeline(case.dir)
    llm = mod.FixtureLLM(fixture) if fixture else mod.mock_llm()
    items = mod.mock_items() if not fixture else fixture["personas"]
    dataset = Dataset("mock", "synthetic", "sha", items)
    result = runner.run_case(
        case, kind="mock", run_no=0, dataset=dataset, llm=llm, pipeline_module=mod
    )
    return case, llm, result


# -- known cost from known rows, at both price tables ---------------------------------------------


def test_the_fixture_gives_the_known_cost_per_design_at_haiku_45_prices(tmp_path):
    _case, _llm, result = mock_run(tmp_path)
    for design, usd in USD_45.items():
        assert result["metrics"][f"usd_per_1000_turns@{design}"] == pytest.approx(usd, abs=1e-9)


def test_the_same_rows_priced_at_haiku_55(tmp_path):
    _case, _llm, result = mock_run(tmp_path)
    for design, usd in USD_55.items():
        assert result["metrics"][f"usd_per_1000_turns@{design}_h55"] == pytest.approx(usd, abs=1e-9)


def test_cost_is_the_rows_priced_once_with_no_further_overhead_subtraction(tmp_path):
    # one realtime row is (1000 in, 100 out) = $0.0015; the whole run's cost is the rows' sum
    _case, _llm, result = mock_run(tmp_path)
    rows = len(FIXTURE["rows"])  # 23; each is seen by its design, by its _h55 view and by `total`
    assert result["cost"]["calls"] == 3 * rows
    assert result["metrics"]["recount_gap@total"] == pytest.approx(0, abs=1e-9)


def test_the_client_subtracts_the_overhead_once_per_call(monkeypatch):
    client = ClaudeCodeLLMClient()
    monkeypatch.setattr(client, "_run", lambda **kw: ("ok", 1000, 10))
    client.overhead["claude-haiku-5-5"] = 480
    result = client.complete(system="s", user="u", model="claude-haiku-5-5")
    assert (result.input_tokens, result.raw_input_tokens) == (520, 1000)


# -- recount_gap ----------------------------------------------------------------------------------


def test_recount_gap_is_the_gap_to_the_ledger_over_the_ledger(tmp_path):
    fixture = copy.deepcopy(FIXTURE)
    fixture["ledger_usd"] = 0.0212100 / 0.99  # the rows sum to 1% below this ledger
    _case, _llm, result = mock_run(tmp_path, fixture)
    assert result["metrics"]["recount_gap@total"] == pytest.approx(0.01, abs=1e-9)


def test_a_missing_ledger_leaves_recount_gap_without_a_value(tmp_path):
    fixture = copy.deepcopy(FIXTURE)
    fixture["ledger_usd"] = 0
    _case, _llm, result = mock_run(tmp_path, fixture)
    assert result["metrics"]["recount_gap@total"] is None


# -- the overhead ---------------------------------------------------------------------------------


def test_overhead_is_the_mean_of_ten_empty_calls_and_they_are_the_only_calls(tmp_path):
    _case, llm, result = mock_run(tmp_path)
    assert result["metrics"]["overhead_tokens@total"] == 480  # mean of the fixture's ten raw counts
    assert len(llm.calls) == 10  # once per run, not once per design or persona
    assert all(c["system"] == "x" and c["user"] == "x" for c in llm.calls)
    assert {c["model"] for c in llm.calls} == {"claude-haiku-5-5"}


# -- the gap to iteration 1 -----------------------------------------------------------------------


def test_usd_vs_iter1_is_the_share_by_which_a_design_differs_from_the_reported_figure(tmp_path):
    _case, _llm, result = mock_run(tmp_path)
    m = result["metrics"]
    assert m["usd_vs_iter1@realtime"] == pytest.approx(abs(1.5 - 1.15) / 1.15)
    assert m["usd_vs_iter1@hybrid_jev"] == pytest.approx(abs(0.792 - 0.68) / 0.68)
    assert m["usd_vs_iter1@explicit"] == 0  # iteration 1 reported 0, and the recount is 0


# -- reading the sibling case's results (live) ----------------------------------------------------


def test_run_n_reads_the_nth_rows_and_ledger_and_runs_are_counted_from_the_results_dir(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    mod = runner.load_pipeline(CASE_DIR)
    assert mod.current_run(results) == 1
    (results / "run1.json").write_text("{}")
    assert mod.current_run(results) == 2
    for n in (2, 3):
        (results / f"run{n}.json").write_text("{}")
    with pytest.raises(RuntimeError):
        mod.current_run(results)

    asked = []

    def git_json(ref, case_id, name):
        asked.append((ref, case_id, name))
        return (
            {"cost": {"usd": 1.0}, "dataset": {"sha256": "abc"}} if name.startswith("run") else []
        )

    mod.current_run = lambda _dir: 2
    mod._git_json = git_json
    source = mod.live_source(json.loads((CASE_DIR / "case.json").read_text())["config"])
    ref, case_id = "origin/lab/reverify/heldout-personas", "reverify/heldout-personas"
    assert asked == [(ref, case_id, "run2.json"), (ref, case_id, "calls_run2.json")]
    assert source.ledger_usd == 1.0


def test_a_dataset_other_than_the_one_the_rows_were_saved_on_is_refused():
    mod = runner.load_pipeline(CASE_DIR)
    mod.live_source = lambda config: mod.Source(1, [], 1.0, "abc")
    mod.select_items([], {"source_dataset": "syn1"}, Dataset("syn1", "synthetic", "abc", []))
    with pytest.raises(RuntimeError):
        mod.select_items([], {"source_dataset": "syn1"}, Dataset("syn1", "synthetic", "other", []))


# -- the manifest as registered -------------------------------------------------------------------


def test_manifest_has_every_keep_if_clause_as_a_bar_and_the_deviations():
    case = load_case(CASE_DIR)
    assert case.datasets == ["syn1"] and case.status == "planned"
    assert case.config["model"] == "claude-haiku-5-5" and case.config["overhead_calls"] == 10
    got = [(b.metric, b.agg, b.op, b.value) for b in case.keep_if]
    assert got == [
        ("overhead_tokens@total", "mean", ">=", 432),
        ("overhead_tokens@total", "mean", "<=", 528),
        ("recount_gap@total", "each", "<=", 0.01),
        ("usd_per_1000_turns@hybrid_jev", "mean", "<=", 1.00),
        *[(f"usd_vs_iter1@{d}", "mean", "<=", 0.15) for d in DESIGNS],
    ]
    text = json.dumps(json.loads((CASE_DIR / "case.json").read_text())["deviations"])
    assert "anon-personas" in text and "git show" in text and "calls_run<n>.json" in text


def fake_result(run_no, overhead=480, gap=0.0, e_usd=0.68, gaps=0.0):
    metrics = {"overhead_tokens@total": overhead, "recount_gap@total": gap,
               "usd_per_1000_turns@hybrid_jev": e_usd}  # fmt: skip
    metrics.update({f"usd_vs_iter1@{d}": gaps for d in DESIGNS})
    return {
        "dataset": {"name": "syn1", "kind": "synthetic", "sha256": "s", "n": 9},
        "metrics": metrics, "ratios": {}, "invalid": 0,
        "privacy": {"sensitive_reached_model": 0, "stated_overwritten": 0, "exclusion_stored": 0},
    }  # fmt: skip


def test_bars_keep_when_the_headline_holds_and_drop_when_it_does_not():
    case = load_case(CASE_DIR)
    runs = [fake_result(n) for n in (1, 2, 3)]
    held = verdict.compute(case, runs)
    assert held["verdict"] == "keep" and {b["state"] for b in held["bars"]} == {"holds"}
    assert (
        verdict.compute(case, [fake_result(n, overhead=600) for n in (1, 2, 3)])["verdict"]
        == "drop"
    )
    assert (
        verdict.compute(case, [fake_result(n, e_usd=1.2) for n in (1, 2, 3)])["verdict"] == "drop"
    )
    assert verdict.compute(case, [fake_result(n, gaps=0.3) for n in (1, 2, 3)])["verdict"] == "drop"
    one_bad = [fake_result(1, gap=0.02), fake_result(2), fake_result(3)]
    assert (
        verdict.compute(case, one_bad)["verdict"] == "inconclusive"
    )  # `each` holds in only two runs


# -- explain --------------------------------------------------------------------------------------


def test_explain_shows_iter1_and_both_recounts_per_design(tmp_path, capsys):
    directory = case_copy(tmp_path)
    (directory / "results").mkdir()
    for n in (1, 2, 3):
        metrics = {"overhead_tokens@total": 480}
        for d in DESIGNS:
            metrics[f"usd_per_1000_turns@{d}"] = USD_45[d]
            metrics[f"usd_per_1000_turns@{d}_h55"] = USD_55[d]
        (directory / "results" / f"run{n}.json").write_text(
            json.dumps({"dataset": {"name": "syn1"}, "metrics": metrics}), encoding="utf-8"
        )
    assert cli.main(["--cases-root", str(tmp_path), "explain", CASE_ID]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "  realtime: iter1 1.15 | haiku45 1.50 | haiku55 0.15" in lines
    assert "  explicit: iter1 0.00 | haiku45 0.00 | haiku55 0.00" in lines
    assert "  hybrid_jev: iter1 0.68 | haiku45 0.79 | haiku55 0.12" in lines
    assert "overhead_tokens (mean of the runs): 480.00" in lines
