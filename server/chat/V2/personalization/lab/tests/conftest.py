"""Fixtures for the lab kit: a throwaway git repository with an `origin`, and case builders."""

import json
import os
import subprocess
from pathlib import Path

import pytest

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}  # fmt: skip

PIPELINE = "from chat.V2.personalization.lab.replay import strategy_pipeline as build\n"

CASE_JSON = {
    "question": "q", "why": "w", "source": "s", "hypothesis": "h", "engine": "offline",
    "task": "replay", "config": {"strategy": "realtime"}, "datasets": ["syn1"],
    "metrics": ["slot_accuracy"], "status": "planned",
    "keep_if": [{"metric": "slot_accuracy", "agg": "mean", "op": ">=", "value": 0.5}],
}  # fmt: skip


def git(cwd, *args):
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env
    ).stdout


def write_case(root: Path, aspect="a", name="x", **overrides) -> Path:
    directory = root / aspect / name
    directory.mkdir(parents=True, exist_ok=True)
    data = {**CASE_JSON, **overrides}
    (directory / "case.json").write_text(json.dumps(data), encoding="utf-8")
    (directory / "pipeline.py").write_text(PIPELINE, encoding="utf-8")
    return directory


@pytest.fixture
def repo(tmp_path):
    """(work tree, cases root inside it): a clone of a bare origin, on branch main, one commit pushed."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    git(work, "checkout", "-q", "-b", "main")
    (work / "README").write_text("x")
    (work / ".gitignore").write_text("__pycache__/\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "init")
    git(work, "push", "-q", "-u", "origin", "main")
    return work, work / "lab" / "cases"


def commit_and_push(work: Path, message="c", push=True, ref="main"):
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", message)
    if push:
        git(work, "push", "-q", "origin", f"HEAD:{ref}")
