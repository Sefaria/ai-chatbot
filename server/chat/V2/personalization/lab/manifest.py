"""A case's manifest (`case.json`) and its pass bar, validated when loaded."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import datasets
from .metrics import METRICS, load_aspect_metrics, metric_for

ENGINES = ("offline", "app")
TASKS = ("replay", "label", "pairs", "script")
STATUSES = ("draft", "planned", "blocked")
AGGS = ("mean", "min", "max", "pooled", "each")
OPS = (">=", "<=", "==", ">")


class ManifestError(ValueError):
    """The case's manifest is not valid."""


@dataclass(frozen=True)
class Bar:
    metric: str  # "name" or "name@variant"
    agg: str = "mean"
    op: str = ">="
    value: float | None = None
    ref: dict | None = None  # {case, metric, agg, factor, offset}
    dataset: str = ""  # default: the case's first dataset
    expected_to_fail: bool = False


@dataclass(frozen=True)
class Case:
    id: str
    aspect: str
    case: str
    question: str
    why: str
    source: str
    hypothesis: str
    engine: str
    task: str
    config: dict
    datasets: list
    metrics: list
    keep_if: list
    cost_estimate: str
    status: str
    blocked_reason: str
    dir: str = field(default="", compare=False)  # the case directory ("" when built by hand)


def parse_bar(raw: dict, default_dataset: str, known: dict) -> Bar:
    allowed = {"metric", "agg", "op", "value", "ref", "dataset", "expected_to_fail"}
    if extra := set(raw) - allowed:
        raise ManifestError(f"bar has unknown fields {sorted(extra)}")
    bar = Bar(**raw)
    if metric_for(bar.metric, known) is None:
        raise ManifestError(f"bar names an unknown metric {bar.metric!r}")
    if bar.agg not in AGGS:
        raise ManifestError(f"bar agg {bar.agg!r} is not one of {AGGS}")
    if bar.op not in OPS:
        raise ManifestError(f"bar op {bar.op!r} is not one of {OPS}")
    if bar.value is None and not bar.ref:
        raise ManifestError(f"bar on {bar.metric} has neither a value nor a ref")
    if bar.ref:
        missing = {"case", "metric"} - set(bar.ref)
        if missing:
            raise ManifestError(f"bar ref lacks {sorted(missing)}")
        if metric_for(bar.ref["metric"], known) is None:
            raise ManifestError(f"bar ref names an unknown metric {bar.ref['metric']!r}")
    return replace(bar, dataset=bar.dataset or default_dataset)


def load_case(case_dir) -> Case:
    case_dir = Path(case_dir)
    try:
        raw = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ManifestError(f"{case_dir}: no case.json") from error
    aspect, name = case_dir.parent.name, case_dir.name
    case_id = f"{aspect}/{name}"
    if raw.get("id", case_id) != case_id:
        raise ManifestError(f"case.json id {raw['id']!r} does not match its directory {case_id!r}")
    aspect_json = case_dir.parent / "aspect.json"
    aspect_raw = (
        json.loads(aspect_json.read_text(encoding="utf-8")) if aspect_json.is_file() else {}
    )
    known = {**METRICS, **load_aspect_metrics(case_dir.parent / "metrics.py")}

    status = raw.get("status", "draft")
    if status not in STATUSES or status == "draft":
        raise ManifestError(f"{case_id}: status {status!r}; a case must be 'planned' or 'blocked'")
    if not str(raw.get("hypothesis", "")).strip():
        raise ManifestError(f"{case_id}: the hypothesis is empty")
    if raw.get("engine") not in ENGINES:
        raise ManifestError(f"{case_id}: engine must be one of {ENGINES}")
    if raw.get("task") not in TASKS:
        raise ManifestError(f"{case_id}: task must be one of {TASKS}")
    if status == "blocked" and not str(raw.get("blocked_reason", "")).strip():
        raise ManifestError(f"{case_id}: a blocked case needs a blocked_reason")
    registered = datasets.registered_names()
    case_datasets = list(raw.get("datasets", []))
    if not case_datasets:
        raise ManifestError(f"{case_id}: no datasets")
    for dataset in case_datasets:
        if dataset not in registered:
            raise ManifestError(f"{case_id}: unknown dataset {dataset!r}")
    metrics = list(raw.get("metrics", []))
    for key in metrics:
        if metric_for(key, known) is None:
            raise ManifestError(f"{case_id}: unknown metric {key!r}")
    bars = [parse_bar(b, case_datasets[0], known) for b in raw.get("keep_if", [])]
    for bar in bars:
        if bar.dataset not in case_datasets:
            raise ManifestError(
                f"{case_id}: bar on {bar.metric} names dataset {bar.dataset!r} the case lacks"
            )
    if status == "planned" and not bars:
        raise ManifestError(f"{case_id}: a planned case needs keep_if bars")
    return Case(
        id=case_id, aspect=aspect, case=name,
        question=raw.get("question", ""), why=raw.get("why", aspect_raw.get("why", "")),
        source=raw.get("source", aspect_raw.get("source", "")), hypothesis=raw["hypothesis"],
        engine=raw["engine"], task=raw["task"], config=dict(raw.get("config", {})),
        datasets=case_datasets, metrics=metrics, keep_if=bars,
        cost_estimate=raw.get("cost_estimate", ""), status=status,
        blocked_reason=raw.get("blocked_reason", ""), dir=str(case_dir),
    )  # fmt: skip


def case_dirs(root: Path) -> list[Path]:
    """Every `<root>/<aspect>/<case>/` that holds a case.json."""
    return sorted(p.parent for p in Path(root).glob("*/*/case.json"))
