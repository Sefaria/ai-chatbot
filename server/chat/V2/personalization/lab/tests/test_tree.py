import json

from chat.V2.personalization.lab import guard, tree
from chat.V2.personalization.lab.__main__ import main

from .conftest import CASE_JSON, git

CASES = "server/chat/V2/personalization/lab/cases"


def _write(work, rel, data):
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def _branch(work, name, files):
    git(work, "checkout", "-q", "-b", name, "main")
    for rel, data in files.items():
        _write(work, rel, data)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", name)
    git(work, "push", "-q", "origin", name)
    git(work, "checkout", "-q", "main")
    git(work, "branch", "-q", "-D", name)


def _result(kind="live", run=1, value=0.5):
    return {
        "id": "a/x", "kind": kind, "run": run, "dataset": {"name": "syn1", "kind": "synthetic", "n": 2},
        "metrics": {"slot_accuracy": value}, "ratios": {"slot_accuracy": [1, 2]},
        "steps": [{"name": "score", "n_in": 2, "n_out": 1}], "cost": {"calls": 3}, "per_item": [],
    }  # fmt: skip


def _fixture(work):
    aspect = {"id": "a", "question": "aq", "why": "aw", "source": "as"}
    x = {
        f"{CASES}/a/aspect.json": aspect,
        f"{CASES}/a/x/case.json": CASE_JSON,
        f"{CASES}/a/x/pipeline.py": "# x\n",
        f"{CASES}/a/x/results/mock.json": _result("mock", 0, 0.4),
        f"{CASES}/a/x/results/run1.json": _result(run=1),
        f"{CASES}/a/x/results/verdict.json": {"verdict": "keep", "bars": []},
    }
    y = {
        f"{CASES}/a/aspect.json": aspect,
        f"{CASES}/a/y/case.json": {
            **CASE_JSON,
            "status": "blocked",
            "blocked_reason": "needs data",
        },
        f"{CASES}/a/y/pipeline.py": "# y\n",
    }
    _branch(work, "lab/a/x", x)
    _branch(work, "lab/a/y", y)


def test_tree_reads_cases_from_remote_branches_without_checkout(repo):
    work, _ = repo
    _fixture(work)
    head = git(work, "rev-parse", "HEAD").strip()
    found = tree.build_tree(work, "origin")
    assert git(work, "rev-parse", "HEAD").strip() == head
    assert not (work / "server").exists()
    assert [a["id"] for a in found["aspects"]] == ["a"]
    aspect = found["aspects"][0]
    assert (aspect["question"], aspect["why"], aspect["source"]) == ("aq", "aw", "as")
    cases = {c["id"]: c for c in aspect["cases"]}
    assert set(cases) == {"a/x", "a/y"}
    x = cases["a/x"]
    assert x["branch"] == "lab/a/x"
    assert x["manifest"]["hypothesis"] == "h"
    assert x["mock"]["metrics"] == {"slot_accuracy": 0.4}
    assert [r["run"] for r in x["runs"]] == [1]
    assert x["real_runs"] == []
    assert x["verdict"]["verdict"] == "keep"
    assert x["status"] == "done"
    assert found["generated_at"] and "core_sha" in found


def test_tree_case_sha_matches_the_guard_hash(repo, tmp_path):
    work, _ = repo
    _fixture(work)
    found = tree.build_tree(work, "origin")
    x = next(c for c in found["aspects"][0]["cases"] if c["id"] == "a/x")
    git(work, "checkout", "-q", "-b", "look", "origin/lab/a/x")
    assert x["case_sha"] == guard.case_sha(work / CASES / "a" / "x")


def test_tree_marks_blocked_case(repo, tmp_path):
    work, _ = repo
    _fixture(work)
    out = tmp_path / "tree.json"
    assert main(["tree", "--remote", "origin", "--out", str(out), "--repo", str(work)]) == 0
    written = json.loads(out.read_text())
    y = next(c for c in written["aspects"][0]["cases"] if c["id"] == "a/y")
    assert y["status"] == "blocked"
    assert y["verdict"] is None
