"""The verdict, computed by code from the result files and the pass bar written before the runs.

Rules (test tree, top):
- Privacy applies to every case: a sensitive turn that reached a model, a stated slot overwritten
  or an exclusion stored, in any run, makes the case `drop`.
- Each bar (not `expected_to_fail`) is `holds` (as written), `fails` (fails in every run, or fails
  on the mean or the pooled figure) or `mixed` (a min, max or each bar that fails while some run
  holds); a bar with no data, fewer than three runs or a missing reference is `inconclusive`.
- `drop` if a bar fails; else `inconclusive` if a bar is mixed or inconclusive, or any run has
  `invalid` above 5% of its items; else `keep`.
A person can add a `note` to verdict.json; the computed verdict is not editable.
"""

from __future__ import annotations

import json
import operator
import statistics
import subprocess
from collections.abc import Callable
from pathlib import Path

from .guard import repo_root

OPS = {">=": operator.ge, "<=": operator.le, "==": lambda a, b: abs(a - b) < 1e-9, ">": operator.gt}
INVALID_SHARE = 0.05
RUNS = 3


def _metrics_for(result: dict, dataset: str) -> dict | None:
    if result["dataset"]["name"] == dataset:
        return result
    return result.get("also", {}).get(dataset)


def _series(results: list[dict], dataset: str, metric: str):
    """([value per run], [ratio per run or None]) for the runs that carry `dataset`."""
    values, ratios = [], []
    for result in results:
        part = _metrics_for(result, dataset)
        if part is None:
            continue
        values.append(part["metrics"].get(metric))
        ratios.append(part.get("ratios", {}).get(metric))
    return values, ratios


def aggregate(values: list, ratios: list, agg: str):
    """One number from the runs' values; None when the runs cannot answer."""
    if not values or any(v is None for v in values):
        return None
    if agg == "min":
        return min(values)
    if agg == "max":
        return max(values)
    if agg == "pooled" and all(r for r in ratios):
        den = sum(r[1] for r in ratios)
        return sum(r[0] for r in ratios) / den if den else None
    return statistics.mean(values)  # mean, pooled without ratios, and `each` (reported as the mean)


def git_ref_reader(case, ref_case_id: str, run_no: int) -> dict | None:
    """The sibling case's result file on `origin/lab/<aspect>/<case>`, read with `git show`."""
    case_dir = Path(case.dir).resolve()
    top = repo_root(case_dir)
    sibling = case_dir.parent.parent / ref_case_id / "results" / f"run{run_no}.json"
    rel = sibling.relative_to(top).as_posix()
    proc = subprocess.run(
        ["git", "show", f"origin/lab/{ref_case_id}:{rel}"], cwd=top, capture_output=True, text=True
    )
    return json.loads(proc.stdout) if proc.returncode == 0 else None


def _target(bar, case, ref_reader):
    """(target, why_not): the number the bar compares with."""
    if not bar.ref:
        return bar.value, ""
    ref = bar.ref
    reader = ref_reader or git_ref_reader
    found = [reader(case, ref["case"], n) for n in range(1, RUNS + 1)]
    if any(r is None for r in found):
        return None, f"reference case {ref['case']} has no results yet"
    dataset = ref.get("dataset", bar.dataset)
    values, ratios = _series(found, dataset, ref["metric"])
    value = aggregate(values, ratios, ref.get("agg", "mean"))
    if value is None:
        return None, f"reference metric {ref['metric']} is missing in {ref['case']}"
    return ref.get("factor", 1.0) * value + ref.get("offset", 0.0), ""


def evaluate_bar(bar, case, results: list[dict], ref_reader=None) -> dict:
    row = {
        "metric": bar.metric, "agg": bar.agg, "op": bar.op, "target": None, "observed": None,
        "holds": None, "state": "inconclusive", "dataset": bar.dataset,
        "expected_to_fail": bar.expected_to_fail, "why": "",
    }  # fmt: skip
    values, ratios = _series(results, bar.dataset, bar.metric)
    target, why = _target(bar, case, ref_reader)
    row["target"] = target
    if why or target is None:
        row["why"] = why or "no target"
        return row
    if len(values) < RUNS:
        row["why"] = f"{len(values)} of {RUNS} runs"
        return row
    if any(v is None for v in values):
        row["why"] = "a run has no value for this metric"
        return row
    op = OPS[bar.op]
    per_run = [bool(op(v, target)) for v in values]
    if bar.agg == "each":
        observed = min(values) if bar.op in (">=", ">") else max(values)
        holds = all(per_run)
    else:
        observed = aggregate(values, ratios, bar.agg)
        holds = bool(op(observed, target))
    row.update(observed=observed, holds=holds)
    if holds:
        row["state"] = "holds"
    elif not any(per_run) or bar.agg in ("mean", "pooled"):
        row["state"] = "fails"
    else:
        row["state"] = "mixed"
    return row


def compute(case, results: list[dict], ref_reader: Callable | None = None) -> dict:
    """`results` are the live result files (synthetic and real) of the case."""
    rows = [evaluate_bar(bar, case, results, ref_reader) for bar in case.keep_if]
    privacy = {"sensitive_reached_model": 0, "stated_overwritten": 0, "exclusion_stored": 0}
    for result in results:
        for key in privacy:
            privacy[key] = max(privacy[key], (result.get("privacy") or {}).get(key) or 0)
    counted = [r for r in rows if not r["expected_to_fail"]]
    invalid_runs = [
        r for r in results
        if r["dataset"]["n"] and r.get("invalid", 0) / r["dataset"]["n"] > INVALID_SHARE
    ]  # fmt: skip
    notes = []
    if any(privacy.values()):
        verdict = "drop"
        notes.append("a privacy bar was broken")
    elif any(r["state"] == "fails" for r in counted):
        verdict = "drop"
    elif any(r["state"] in ("mixed", "inconclusive") for r in counted) or invalid_runs:
        verdict = "inconclusive"
        if invalid_runs:
            notes.append("invalid answers above 5% of items in a run")
    else:
        verdict = "keep"
    if not results:
        verdict = "inconclusive"
        notes.append("no runs")
    return {
        "verdict": verdict,
        "bars": [{k: r[k] for k in ("metric", "agg", "op", "target", "observed", "holds", "state", "dataset", "expected_to_fail", "why")} for r in rows],
        "privacy": privacy,
        "note": "",
        "reasons": notes,
    }  # fmt: skip
