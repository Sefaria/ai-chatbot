"""Result files, and the rule that a real-data result carries numbers only."""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import datasets
from .steps import STEPS

# String fields a real-dataset result may carry (besides `dataset` strings and `steps[].name`).
ALLOWED_STRINGS = ("id", "kind", "model", "started_at", "case_sha", "git_sha")
MAX_STRING = 200


class RealDataLeak(ValueError):
    """A result for a real dataset would carry per-item rows or free text."""


TOP_KEYS = {
    "schema", "id", "kind", "run", "model", "dataset", "case_sha", "git_sha", "started_at",
    "metrics", "ratios", "invalid", "cost", "steps", "privacy", "also",
}  # fmt: skip
PRIVACY_KEYS = {"sensitive_reached_model", "stated_overwritten", "exclusion_stored"}
COST_KEYS = {"calls", "in_tokens", "out_tokens", "typed_requests", "usd"}
DATASET_KEYS = {"name", "kind", "sha256", "n"}
STEP_KEYS = {"name", "n_in", "n_out"}
ALSO_KEYS = {"dataset", "metrics", "ratios", "invalid", "privacy"}
_PLAIN = re.compile(r"^[A-Za-z0-9_./-]{1,60}$")


def _check_dataset(value, path: str) -> None:
    if not isinstance(value, dict):
        raise RealDataLeak(f"{path} is not a dataset record")
    for key, inner in value.items():
        if key not in DATASET_KEYS:
            raise RealDataLeak(f"unknown key {key!r} in {path}")
        if not isinstance(inner, (str, int)) or len(str(inner)) > MAX_STRING:
            raise RealDataLeak("dataset field carries more than names and counts")


def check_real(result: dict, known_metrics=None) -> None:
    """Raise RealDataLeak unless `result` (a real-dataset result) is numbers plus the fixed fields.
    Keys are free text too: each dict key must be a known name (metric, privacy, cost, step, field)."""
    from .metrics import METRICS, split_key

    metric_names = set(METRICS) | set(known_metrics or ())
    if "per_item" in result:
        raise RealDataLeak("a real-dataset result must not carry per_item")

    def metric_key(key: str, path: str) -> None:
        name, param = split_key(key)
        if name not in metric_names or (param is not None and not _PLAIN.match(param)):
            raise RealDataLeak(f"key {key!r} in {path} is not a known metric name")

    def walk(value, path: str, keys=None, metric_keys=False) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                if isinstance(inner, str):
                    raise RealDataLeak(f"string value at {path}.{key} in a real-dataset result")
                if metric_keys:
                    metric_key(key, path)
                elif keys is not None and key not in keys:
                    raise RealDataLeak(f"unknown key {key!r} in {path}")
                walk(inner, f"{path}.{key}")
        elif isinstance(value, list):
            for i, inner in enumerate(value):
                walk(inner, f"{path}[{i}]")
        elif isinstance(value, str):
            raise RealDataLeak(f"string value at {path} in a real-dataset result")

    def numbers(part: dict, path: str) -> None:
        for key, value in part.items():
            if key in ("metrics", "ratios"):
                walk(value, f"{path}{key}", metric_keys=True)
            elif key == "privacy":
                walk(value, f"{path}{key}", keys=PRIVACY_KEYS)
            elif key != "dataset":
                walk(value, f"{path}{key}")

    for key, value in result.items():
        if key not in TOP_KEYS:
            if isinstance(value, str):
                raise RealDataLeak(f"string value at {key} in a real-dataset result")
            raise RealDataLeak(f"unknown key {key!r} in a real-dataset result")
        if key in ALLOWED_STRINGS:
            if not isinstance(value, str) or len(value) > MAX_STRING:
                raise RealDataLeak(f"field {key} is not a short string")
        elif key == "dataset":
            _check_dataset(value, "dataset")
        elif key == "steps":
            for step in value:
                if step.get("name") not in STEPS:
                    raise RealDataLeak(f"step name {step.get('name')!r} is not in the catalog")
                walk({k: v for k, v in step.items() if k != "name"}, "steps", keys=STEP_KEYS)
        elif key == "also":
            for name, part in value.items():
                if not _PLAIN.match(str(name)):
                    raise RealDataLeak(f"key {name!r} in also is not a dataset name")
                if set(part) - ALSO_KEYS:
                    raise RealDataLeak(f"unknown key in also.{name}")
                _check_dataset(part.get("dataset", {}), f"also.{name}.dataset")
                numbers(part, f"also.{name}.")
        elif key in ("metrics", "ratios"):
            walk(value, key, metric_keys=True)
        elif key == "privacy":
            walk(value, key, keys=PRIVACY_KEYS)
        elif key == "cost":
            walk(value, key, keys=COST_KEYS)
        else:
            walk(value, key)


def is_real(result: dict) -> bool:
    return result.get("dataset", {}).get("kind") == "real" or any(
        part.get("dataset", {}).get("kind") == "real" for part in result.get("also", {}).values()
    )


def file_name(kind: str, run_no: int, real: bool) -> str:
    if kind == "mock":
        return "mock.json"
    return f"real_run{run_no}.json" if real else f"run{run_no}.json"


def write(case_dir, result: dict, name: str | None = None) -> Path:
    """Write a result file under `<case_dir>/results/`; refuse a real-dataset result that leaks text."""
    real = is_real(result)
    if real:
        from .metrics import load_aspect_metrics

        check_real(result, load_aspect_metrics(Path(case_dir).parent / "metrics.py"))
    out = Path(case_dir) / "results" / (name or file_name(result["kind"], result["run"], real))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    return out


def read(case_dir, name: str) -> dict | None:
    path = Path(case_dir) / "results" / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def partial_dir(case_id: str, case_dir, real: bool) -> Path:
    """Where run progress lives: outside every repository for real data, under the ignored
    `results/.partial/` for synthetic."""
    if real:
        return datasets.data_dir() / "partial" / case_id
    return Path(case_dir) / "results" / ".partial"
