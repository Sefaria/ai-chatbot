#!/usr/bin/env bash
# Cut a lab branch. Usage:
#   lab_branch.sh aspect <aspect> "<question>" "<why>" "<source>"   (from lab/core; commits and pushes)
#   lab_branch.sh case <aspect> <case>                               (from lab/<aspect>/aspect; no commit)
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
LAB="server/chat/V2/personalization/lab"
CASES="$ROOT/$LAB/cases"

die() { echo "lab_branch: $*" >&2; exit 1; }

[ -z "$(git -C "$ROOT" status --porcelain)" ] || die "working tree is dirty; commit or stash first"

cmd="${1:-}"
case "$cmd" in
  aspect)
    [ $# -eq 5 ] || die 'usage: lab_branch.sh aspect <aspect> "<question>" "<why>" "<source>"'
    aspect="$2"; question="$3"; why="$4"; source="$5"
    [[ "$aspect" =~ ^[a-z0-9-]+$ ]] || die "aspect must be lowercase letters, digits and hyphens: $aspect"
    branch="lab/$aspect/aspect"
    git -C "$ROOT" show-ref --verify --quiet "refs/heads/$branch" && die "$branch already exists"
    git -C "$ROOT" checkout -q lab/core
    git -C "$ROOT" pull -q --ff-only origin lab/core
    git -C "$ROOT" checkout -q -b "$branch"
    mkdir -p "$CASES/$aspect"
    python3 - "$CASES/$aspect/aspect.json" "$aspect" "$question" "$why" "$source" <<'PY'
import json, sys
path, aspect, question, why, source = sys.argv[1:6]
with open(path, "w", encoding="utf-8") as f:
    json.dump({"id": aspect, "question": question, "why": why, "source": source}, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
    git -C "$ROOT" add "$LAB/cases/$aspect/aspect.json"
    git -C "$ROOT" commit -q -m "lab($aspect): aspect question"
    git -C "$ROOT" push -q -u origin "$branch"
    echo "created $branch"
    ;;
  case)
    [ $# -eq 3 ] || die "usage: lab_branch.sh case <aspect> <case>"
    aspect="$2"; case_id="$3"
    [[ "$aspect" =~ ^[a-z0-9-]+$ && "$case_id" =~ ^[a-z0-9-]+$ ]] || die "names must be lowercase letters, digits and hyphens"
    parent="lab/$aspect/aspect"
    branch="lab/$aspect/$case_id"
    git -C "$ROOT" show-ref --verify --quiet "refs/heads/$parent" || die "$parent does not exist; run: lab_branch.sh aspect first"
    git -C "$ROOT" show-ref --verify --quiet "refs/heads/$branch" && die "$branch already exists"
    git -C "$ROOT" checkout -q "$parent"
    git -C "$ROOT" checkout -q -b "$branch"
    dir="$CASES/$aspect/$case_id"
    mkdir -p "$dir/results"
    printf '{\n  "id": "%s/%s",\n  "status": "draft"\n}\n' "$aspect" "$case_id" > "$dir/case.json"
    : > "$dir/pipeline.py"
    : > "$dir/test_mock.py"
    : > "$dir/results/.gitkeep"
    echo "created $branch with $LAB/cases/$aspect/$case_id (not committed)"
    ;;
  *)
    die 'usage: lab_branch.sh aspect <aspect> "<question>" "<why>" "<source>" | lab_branch.sh case <aspect> <case>'
    ;;
esac
