import json

import pytest

from chat.V2.personalization.lab import verdict
from chat.V2.personalization.lab.manifest import Bar, Case, load_case

from .conftest import commit_and_push, git, write_case


def make_case(*bars, case_id="a/x"):
    aspect, name = case_id.split("/")
    return Case(
        id=case_id, aspect=aspect, case=name, question="q", why="w", source="s", hypothesis="h",
        engine="offline", task="replay", config={}, datasets=["syn1"], metrics=["slot_accuracy"],
        keep_if=list(bars), cost_estimate="", status="planned", blocked_reason="",
    )  # fmt: skip


def result(run, value, ratio=None, privacy=None, invalid=0, metric="slot_accuracy"):
    return {
        "run": run, "dataset": {"name": "syn1", "kind": "synthetic", "sha256": "s", "n": 10},
        "metrics": {metric: value}, "ratios": {metric: ratio} if ratio else {}, "invalid": invalid,
        "privacy": privacy or {"sensitive_reached_model": 0, "stated_overwritten": 0, "exclusion_stored": 0},
    }  # fmt: skip


def runs(*values, **kw):
    return [result(i + 1, v, **kw) for i, v in enumerate(values)]


MEAN_BAR = Bar("slot_accuracy", "mean", ">=", 0.9, dataset="syn1")


def test_verdict_keep_drop_inconclusive():
    keep = verdict.compute(make_case(MEAN_BAR), runs(0.95, 0.93, 0.92))
    drop = verdict.compute(make_case(MEAN_BAR), runs(0.5, 0.6, 0.7))
    min_bar = Bar("slot_accuracy", "min", ">=", 0.9, dataset="syn1")
    mixed = verdict.compute(make_case(min_bar), runs(0.95, 0.95, 0.85))  # holds in some runs only
    assert (keep["verdict"], drop["verdict"], mixed["verdict"]) == ("keep", "drop", "inconclusive")
    assert (
        keep["bars"][0]["observed"] == pytest.approx((0.95 + 0.93 + 0.92) / 3)
        and keep["bars"][0]["holds"] is True
    )
    assert set(keep["bars"][0]) >= {"metric", "agg", "op", "target", "observed", "holds"}
    assert keep["privacy"] == {
        "sensitive_reached_model": 0,
        "stated_overwritten": 0,
        "exclusion_stored": 0,
    }


def test_a_bar_that_fails_on_the_mean_drops_even_if_one_run_holds():
    assert verdict.compute(make_case(MEAN_BAR), runs(0.95, 0.80, 0.80))["verdict"] == "drop"


def test_each_bar_failing_in_one_run_is_mixed_and_in_all_runs_is_a_drop():
    each = Bar("slot_accuracy", "each", ">=", 0.9, dataset="syn1")
    assert verdict.compute(make_case(each), runs(0.95, 0.95, 0.5))["verdict"] == "inconclusive"
    assert verdict.compute(make_case(each), runs(0.5, 0.5, 0.5))["verdict"] == "drop"
    assert verdict.compute(make_case(each), runs(0.95, 0.95, 0.95))["verdict"] == "keep"


def test_pooled_adds_numerators_and_denominators():
    bar = Bar("slot_accuracy", "pooled", ">=", 0.75, dataset="syn1")
    rows = [result(1, 0.5, [1, 2]), result(2, 0.8, [8, 10]), result(3, 0.8, [8, 10])]
    out = verdict.compute(make_case(bar), rows)
    assert out["bars"][0]["observed"] == 17 / 22 and out["verdict"] == "keep"


def test_pooled_on_a_count_adds_the_runs_together():
    bar = Bar("outlier_flips", "pooled", "<=", 1, dataset="syn1")
    rows = [result(n, flips, metric="outlier_flips") for n, flips in ((1, 1), (2, 1), (3, 0))]
    out = verdict.compute(make_case(bar), rows)
    assert out["bars"][0]["observed"] == 2 and out["verdict"] == "drop"  # a mean of 0.67 would keep


def test_privacy_breach_forces_drop():
    breach = {"sensitive_reached_model": 0, "stated_overwritten": 1, "exclusion_stored": 0}
    out = verdict.compute(make_case(MEAN_BAR), runs(0.99, 0.99, 0.99, privacy=breach))
    assert out["verdict"] == "drop" and out["privacy"]["stated_overwritten"] == 1


def test_expected_to_fail_bar_reported_not_counted():
    expected = Bar("slot_accuracy", "mean", ">=", 0.99, dataset="syn1", expected_to_fail=True)
    out = verdict.compute(make_case(MEAN_BAR, expected), runs(0.95, 0.95, 0.95))
    assert out["verdict"] == "keep"
    assert out["bars"][1]["holds"] is False and out["bars"][1]["expected_to_fail"] is True


def test_invalid_above_five_percent_makes_a_keep_inconclusive():
    out = verdict.compute(make_case(MEAN_BAR), runs(0.95, 0.95, 0.95, invalid=1))  # 1 of 10 items
    assert out["verdict"] == "inconclusive"


def test_fewer_than_three_runs_is_inconclusive():
    assert verdict.compute(make_case(MEAN_BAR), runs(0.95, 0.95))["verdict"] == "inconclusive"


def test_a_null_metric_is_inconclusive_not_a_pass():
    assert verdict.compute(make_case(MEAN_BAR), runs(None, 0.95, 0.95))["verdict"] == "inconclusive"


def test_a_bar_on_the_second_dataset_reads_the_also_part():
    bar = Bar("slot_accuracy", "mean", ">=", 0.9, dataset="syn2")
    rows = runs(0.1, 0.1, 0.1)
    for r in rows:
        r["also"] = {
            "syn2": {
                "dataset": {"name": "syn2"},
                "metrics": {"slot_accuracy": 0.95},
                "ratios": {},
                "invalid": 0,
            }
        }
    assert verdict.compute(make_case(bar), rows)["verdict"] == "keep"


def test_ref_bar_missing_is_inconclusive():
    ref = {"case": "a/y", "metric": "slot_accuracy", "factor": 0.5}
    bar = Bar("slot_accuracy", "mean", "<=", ref=ref, dataset="syn1")
    out = verdict.compute(
        make_case(bar), runs(0.1, 0.1, 0.1), ref_reader=lambda case, ref_id, n: None
    )
    assert out["verdict"] == "inconclusive" and "no results" in out["bars"][0]["why"]


def test_ref_bar_uses_factor_and_offset_with_an_injected_reader():
    ref = {"case": "a/y", "metric": "slot_accuracy", "factor": 0.5, "offset": 0.1}
    bar = Bar("slot_accuracy", "mean", "<=", ref=ref, dataset="syn1")
    sibling = lambda case, ref_id, n: result(n, 0.8)  # noqa: E731
    out = verdict.compute(make_case(bar), runs(0.45, 0.5, 0.5), ref_reader=sibling)
    assert out["bars"][0]["target"] == 0.5 and out["verdict"] == "keep"  # 0.5 x 0.8 + 0.1


def test_ref_bar_reads_sibling_branch(repo):
    work, cases = repo
    sibling = write_case(cases, name="y")
    commit_and_push(work)
    git(work, "checkout", "-q", "-b", "lab/a/y")
    (sibling / "results").mkdir()
    for n in (1, 2, 3):
        (sibling / "results" / f"run{n}.json").write_text(json.dumps(result(n, 0.8)))
    commit_and_push(work, "results", ref="lab/a/y")
    git(work, "checkout", "-q", "main")
    mine = write_case(
        cases, name="x",
        keep_if=[{"metric": "slot_accuracy", "op": "<=", "ref": {"case": "a/y", "metric": "slot_accuracy", "factor": 0.5}}],
    )  # fmt: skip
    case = load_case(mine)
    assert not (sibling / "results").exists()  # the sibling's results are on its branch only
    out = verdict.compute(case, runs(0.3, 0.4, 0.4))
    assert out["bars"][0]["target"] == 0.4 and out["verdict"] == "keep"
    assert verdict.compute(case, runs(0.5, 0.5, 0.5))["verdict"] == "drop"


def test_privacy_breach_on_secondary_dataset_forces_drop():
    breach = {"sensitive_reached_model": 0, "stated_overwritten": 0, "exclusion_stored": 2}
    rows = runs(0.99, 0.99, 0.99)
    rows[1]["also"] = {
        "syn2": {
            "dataset": {"name": "syn2", "kind": "synthetic", "sha256": "t", "n": 10},
            "metrics": {}, "ratios": {}, "invalid": 0, "privacy": breach,
        }
    }  # fmt: skip
    out = verdict.compute(make_case(MEAN_BAR), rows)
    assert out["verdict"] == "drop" and out["privacy"]["exclusion_stored"] == 2


def test_invalid_on_secondary_dataset_makes_a_keep_inconclusive():
    rows = runs(0.99, 0.99, 0.99)
    rows[0]["also"] = {
        "syn2": {
            "dataset": {"name": "syn2", "kind": "synthetic", "sha256": "t", "n": 10},
            "metrics": {}, "ratios": {}, "invalid": 5, "privacy": {},
        }
    }  # fmt: skip
    assert verdict.compute(make_case(MEAN_BAR), rows)["verdict"] == "inconclusive"


def cross_runs(*last, metric="runs_identical", kind="synthetic"):
    """Runs 1 and 2 carry None (cross-run metrics compare earlier runs), run 3 carries `last`."""
    rows = [
        result(1, None, metric=metric),
        result(2, None, metric=metric),
        result(3, last[0], metric=metric),
    ]
    for r in rows:
        r["dataset"]["kind"] = kind
    return rows


@pytest.mark.parametrize("agg", ["mean", "min", "each", "pooled"])
def test_cross_run_bar_holds_on_the_last_run_whatever_agg_says(agg):
    bar = Bar("runs_identical", agg, ">=", 1, dataset="syn1")
    assert verdict.compute(make_case(bar), cross_runs(1.0))["verdict"] == "keep"


def test_cross_run_bar_drops_when_the_runs_differ():
    bar = Bar("runs_identical", "mean", ">=", 1, dataset="syn1")
    out = verdict.compute(make_case(bar), cross_runs(0.0))
    assert out["verdict"] == "drop" and out["bars"][0]["observed"] == 0.0


def test_cross_run_bar_is_inconclusive_when_run_3_has_no_value():
    bar = Bar("run_agreement", "mean", ">=", 0.9, dataset="syn1")
    out = verdict.compute(make_case(bar), cross_runs(None, metric="run_agreement"))
    assert out["verdict"] == "inconclusive" and "last run" in out["bars"][0]["why"]


def test_cross_run_bar_on_a_real_dataset_reads_the_last_run():
    bar = Bar("run_agreement", "mean", ">=", 0.9, dataset="syn1")
    out = verdict.compute(make_case(bar), cross_runs(0.95, metric="run_agreement", kind="real"))
    assert out["verdict"] == "keep"


def test_a_non_cross_run_metric_still_needs_every_run():
    assert verdict.compute(make_case(MEAN_BAR), runs(None, None, 0.95))["verdict"] == "inconclusive"
