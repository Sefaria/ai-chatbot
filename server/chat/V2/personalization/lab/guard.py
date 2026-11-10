"""Pre-registration: a case may be run live only if its files are committed and pushed.

`case_sha` is the git tree hash of the case directory without `results/`, computed from the
working tree so a changed file changes it. `assert_preregistered` refuses a case with a modified
or untracked file outside `results/`, or whose last commit touching those files is not on an
`origin` ref.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


class NotPreregistered(RuntimeError):
    """The case directory is dirty, unpushed or not yet committed."""


def _git(args: list[str], cwd) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise NotPreregistered(f"git {args[0]} failed in {cwd}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def repo_root(path) -> Path:
    return Path(_git(["rev-parse", "--show-toplevel"], Path(path)).strip())


def _blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _tree(node: dict) -> str:
    entries = []
    for name, value in node.items():
        if isinstance(value, dict):
            entries.append((name + "/", b"40000", name, _tree(value)))
        else:
            entries.append((name, value[0], name, value[1]))
    body = b"".join(
        mode + b" " + name.encode() + b"\0" + bytes.fromhex(sha)
        for _k, mode, name, sha in sorted(entries, key=lambda e: e[0].encode())
    )
    return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()


def case_files(case_dir) -> list[str]:
    """Tracked and untracked-not-ignored files of the case directory, outside `results/`."""
    case_dir = Path(case_dir)
    out = _git(["ls-files", "-co", "--exclude-standard", "-z", "--", "."], case_dir)
    names = [n for n in out.split("\0") if n and not n.startswith("results/")]
    return sorted({n for n in names if (case_dir / n).is_file()})


def case_sha(case_dir) -> str:
    case_dir = Path(case_dir)
    root: dict = {}
    for name in case_files(case_dir):
        path = case_dir / name
        mode = b"100755" if os.access(path, os.X_OK) else b"100644"
        node = root
        *dirs, leaf = name.split("/")
        for part in dirs:
            node = node.setdefault(part, {})
        node[leaf] = (mode, _blob(path.read_bytes()))
    return _tree(root)


def assert_preregistered(case_dir) -> str:
    case_dir = Path(case_dir).resolve()
    top = repo_root(case_dir)
    rel = case_dir.relative_to(top).as_posix()
    status = _git(["status", "--porcelain", "-z", "--untracked-files=all", "--", rel], top)
    dirty = [
        entry[3:]
        for entry in status.split("\0")
        if len(entry) > 3 and not entry[3:].startswith(f"{rel}/results/")
    ]
    if dirty:
        raise NotPreregistered(f"uncommitted files in {rel}: {sorted(dirty)[:5]}")
    last = _git(["log", "-1", "--format=%H", "--", rel, f":(exclude){rel}/results"], top).strip()
    if not last:
        raise NotPreregistered(f"{rel} has no commit yet")
    pushed = _git(
        ["for-each-ref", "--contains", last, "--format=%(refname)", "refs/remotes/origin"], top
    ).strip()
    if not pushed:
        raise NotPreregistered(f"{rel}: commit {last[:9]} is not on any origin branch; push first")
    return case_sha(case_dir)


def head_sha(path) -> str:
    try:
        return _git(["rev-parse", "HEAD"], Path(path)).strip()
    except NotPreregistered:
        return ""
