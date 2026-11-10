"""Datasets: synthetic ones live in the repository with a frozen checksum; real ones never do.

A synthetic dataset is registered in `data/datasets.json` with the files that make it up and a
sha256 over those files. `load_dataset` recomputes it and raises `DatasetChanged` on a mismatch.
A real dataset is read only from `$LA_LAB_DATA_DIR/<name>/` (items in `items.json`, checksum in
`sha256`); nothing about a real dataset except its name and kind is in the repository.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
REGISTRY_PATH = DATA_DIR / "datasets.json"


class DatasetChanged(RuntimeError):
    """A synthetic dataset's files no longer match the checksum recorded in datasets.json."""


class DatasetError(RuntimeError):
    """The dataset is unknown, not frozen, or not readable."""


@dataclass(frozen=True)
class Dataset:
    name: str
    kind: str  # "synthetic" | "real"
    sha256: str
    items: list
    anonymized: bool = False
    files: dict = field(default_factory=dict, compare=False, repr=False)  # file stem -> parsed JSON


def registry(path: Path | None = None) -> dict[str, dict]:
    return json.loads((path or REGISTRY_PATH).read_text(encoding="utf-8"))


def registered_names(path: Path | None = None) -> list[str]:
    return list(registry(path))


def checksum(directory: Path, files: list[str]) -> str:
    """sha256 over the named files: for each, its name, a NUL byte and its bytes, in name order."""
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode() + b"\0" + (directory / name).read_bytes())
    return digest.hexdigest()


def data_dir() -> Path:
    value = os.environ.get("LA_LAB_DATA_DIR", "")
    if not value:
        raise DatasetError("LA_LAB_DATA_DIR is not set; a real dataset is read only from there")
    return Path(value).expanduser()


def _items(parsed, key: str | None):
    if isinstance(parsed, dict):
        if key and key in parsed:
            return parsed[key]
        for k in ("items", "personas", "prompts", "fixtures"):
            if k in parsed and isinstance(parsed[k], list):
                return parsed[k]
        for v in parsed.values():
            if isinstance(v, list):
                return v
        return next(iter(parsed.values()))
    return parsed


def _read_real_items(directory: Path) -> list:
    labeled = directory / "labeled.jsonl"
    if labeled.is_file():
        return [
            json.loads(line)
            for line in labeled.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    for name in ("items.json", "personas.json", "prompts.json", "fixtures.json"):
        path = directory / name
        if path.is_file():
            return _items(json.loads(path.read_text(encoding="utf-8")), "items")
    for name in ("items.jsonl", "users.jsonl", "unlabeled.jsonl"):
        path = directory / name
        if path.is_file():
            return [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    raise DatasetError(f"no dataset items found in {directory}")


def load_dataset(name: str, *, registry_path: Path | None = None) -> Dataset:
    entries = registry(registry_path)
    if name not in entries:
        raise DatasetError(f"unknown dataset {name!r}; registered: {sorted(entries)}")
    entry = entries[name]
    if entry["kind"] == "real":
        directory = data_dir() / name
        if not directory.is_dir():
            raise DatasetError(f"real dataset {name!r} is not in {directory}")
        sha = (directory / "sha256").read_text(encoding="utf-8").strip()
        meta_path = directory / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        anonymized = bool(meta.get("anonymized") is True)
        return Dataset(name, "real", sha, _read_real_items(directory), anonymized=anonymized)
    directory = (registry_path.parent if registry_path else DATA_DIR) / entry["dir"]
    if not entry.get("sha256"):
        raise DatasetError(
            f"synthetic dataset {name!r} is not frozen yet (no sha256 in datasets.json)"
        )
    actual = checksum(directory, entry["files"])
    if actual != entry["sha256"]:
        raise DatasetChanged(
            f"{name}: files hash to {actual}, datasets.json says {entry['sha256']}"
        )
    files = {
        Path(f).stem: json.loads((directory / f).read_text(encoding="utf-8"))
        for f in entry["files"]
    }
    primary = files[Path(entry["items_file"]).stem]
    return Dataset(name, "synthetic", actual, _items(primary, entry.get("items_key")), files=files)
