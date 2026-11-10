"""Result files, and the rule that a real-data result carries numbers only."""

from __future__ import annotations

import json
from pathlib import Path

from . import datasets
from .steps import STEPS

# String fields a real-dataset result may carry (besides `dataset` strings and `steps[].name`).
ALLOWED_STRINGS = ("id", "kind", "model", "started_at", "case_sha", "git_sha")
MAX_STRING = 200


class RealDataLeak(ValueError):
    """A result for a real dataset would carry per-item rows or free text."""


def check_real(result: dict) -> None:
    """Raise RealDataLeak unless `result` (a real-dataset result) is numbers plus the fixed fields."""
    if "per_item" in result:
        raise RealDataLeak("a real-dataset result must not carry per_item")

    def walk(value, path: str) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                walk(inner, f"{path}.{key}")
        elif isinstance(value, list):
            for i, inner in enumerate(value):
                walk(inner, f"{path}[{i}]")
        elif isinstance(value, str):
            raise RealDataLeak(f"string value at {path} in a real-dataset result")

    for key, value in result.items():
        if key in ALLOWED_STRINGS:
            if not isinstance(value, str) or len(value) > MAX_STRING:
                raise RealDataLeak(f"field {key} is not a short string")
        elif key == "dataset":
            if not all(
                isinstance(v, (str, int)) and len(str(v)) <= MAX_STRING for v in value.values()
            ):
                raise RealDataLeak("dataset field carries more than names and counts")
        elif key == "steps":
            for step in value:
                if step.get("name") not in STEPS:
                    raise RealDataLeak(f"step name {step.get('name')!r} is not in the catalog")
                walk({k: v for k, v in step.items() if k != "name"}, "steps")
        elif key == "also":
            for name, part in value.items():
                walk({k: v for k, v in part.items() if k != "dataset"}, f"also.{name}")
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
    if is_real(result):
        check_real(result)
    real = is_real(result)
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
