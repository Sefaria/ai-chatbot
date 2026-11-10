"""The tree generator: reads every `lab/<aspect>/<case>` branch of a remote with `git show`.

No branch is checked out. The result is the JSON the renderer turns into the clickable tree and the
wiki pages: `{generated_at, core_sha, repo_url, aspects: [{id, question, why, source, branch,
cases: [{id, branch, pr_url, manifest, mock, runs, real_runs, verdict, status, case_sha}]}]}`.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .guard import _tree

CASES_REL = "server/chat/V2/personalization/lab/cases"
BRANCH = re.compile(r"^lab/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
RUN_FILES = ("run1.json", "run2.json", "run3.json")
REAL_FILES = ("real_run1.json", "real_run2.json", "real_run3.json")


def _git(repo, *args: str) -> str | None:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def _show_json(repo, ref: str, path: str):
    out = _git(repo, "show", f"{ref}:{path}")
    if out is None:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def _case_sha(repo, ref: str, rel: str) -> str:
    """The lab's case hash (guard.case_sha) of a case directory on `ref`, without `results/`."""
    out = _git(repo, "ls-tree", "-r", "-z", ref, "--", rel)
    if not out:
        return ""
    root: dict = {}
    for entry in filter(None, out.split("\0")):
        meta, _, path = entry.partition("\t")
        mode, _kind, sha = meta.split()
        name = path[len(rel) + 1 :]
        if name.startswith("results/"):
            continue
        node = root
        *dirs, leaf = name.split("/")
        for part in dirs:
            node = node.setdefault(part, {})
        node[leaf] = (mode.encode(), sha)
    return _tree(root)


def _status(manifest: dict | None, mock, runs, real_runs, verdict) -> str:
    if manifest and manifest.get("status") == "blocked":
        return "blocked"
    if verdict:
        return "done"
    if mock or runs or real_runs:
        return "running"
    return "planned"


def _pr_urls(repo) -> dict[str, str]:
    proc = subprocess.run(
        ["gh", "pr", "list", "--state", "all", "--limit", "500", "--json", "headRefName,url"],
        cwd=repo, capture_output=True, text=True,
    )  # fmt: skip
    if proc.returncode != 0:
        return {}
    urls: dict[str, str] = {}
    for row in json.loads(proc.stdout or "[]"):
        urls.setdefault(row["headRefName"], row["url"])
    return urls


def _repo_url(repo, remote: str) -> str:
    url = (_git(repo, "remote", "get-url", remote) or "").strip()
    url = re.sub(r"^git@github\.com:", "https://github.com/", url)
    return re.sub(r"\.git$", "", url) if url.startswith("https://") else ""


def build_tree(repo, remote: str = "origin", with_prs: bool = False) -> dict:
    repo = Path(repo)
    refs = (
        _git(repo, "for-each-ref", "--format=%(refname:short)", f"refs/remotes/{remote}/lab") or ""
    ).split()
    prefix = f"{remote}/"
    names = sorted(r[len(prefix) :] for r in refs if r.startswith(prefix))
    prs = _pr_urls(repo) if with_prs else {}
    aspects: dict[str, dict] = {}
    for branch in names:
        match = BRANCH.match(branch)
        if not match:
            continue
        aspect_id, case_name = match.groups()
        ref = f"{remote}/{branch}"
        aspect = aspects.setdefault(
            aspect_id, {"id": aspect_id, "question": "", "why": "", "source": "", "branch": None, "cases": []}
        )  # fmt: skip
        aspect_json = _show_json(repo, ref, f"{CASES_REL}/{aspect_id}/aspect.json")
        if case_name == "aspect":
            aspect["branch"] = branch
        if aspect_json and not aspect["question"]:
            aspect.update({k: aspect_json.get(k, "") for k in ("question", "why", "source")})
        if case_name == "aspect":
            continue
        rel = f"{CASES_REL}/{aspect_id}/{case_name}"
        manifest = _show_json(repo, ref, f"{rel}/case.json")
        if manifest is None:
            continue
        read = lambda name: _show_json(repo, ref, f"{rel}/results/{name}")  # noqa: E731
        mock = read("mock.json")
        runs = [r for r in map(read, RUN_FILES) if r]
        real_runs = [r for r in map(read, REAL_FILES) if r]
        verdict = read("verdict.json")
        aspect["cases"].append({
            "id": f"{aspect_id}/{case_name}", "branch": branch, "pr_url": prs.get(branch),
            "manifest": manifest, "mock": mock, "runs": runs, "real_runs": real_runs,
            "verdict": verdict, "status": _status(manifest, mock, runs, real_runs, verdict),
            "case_sha": _case_sha(repo, ref, rel),
        })  # fmt: skip
    for aspect in aspects.values():
        aspect["cases"].sort(key=lambda c: c["id"])
    core = (_git(repo, "rev-parse", f"{remote}/lab/core") or "").strip()
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "core_sha": core, "repo_url": _repo_url(repo, remote),
        "aspects": sorted(aspects.values(), key=lambda a: a["id"]),
    }  # fmt: skip


def write_tree(repo, remote: str, out, with_prs: bool = False) -> dict:
    data = build_tree(repo, remote, with_prs)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data
