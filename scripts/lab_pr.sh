#!/usr/bin/env bash
# Open a draft PR for a lab branch. Usage: lab_pr.sh <branch>
#   lab/core             -> base main,              title "lab: core", body = first section of the lab README
#   lab/<aspect>/aspect  -> base lab/core,          title "lab: <aspect>", body = question and why
#   lab/<aspect>/<case>  -> base lab/<aspect>/aspect, title "lab: <aspect>/<case>", body = LAB explain <id>
# No reviewers. If a PR for the branch is open already, prints its URL and exits 0.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
LAB="server/chat/V2/personalization/lab"
CLAUDE_LINE='🤖 Generated with [Claude Code](https://claude.com/claude-code)'

die() { echo "lab_pr: $*" >&2; exit 1; }

branch="${1:-}"
[ -n "$branch" ] || die "usage: lab_pr.sh <branch>"
[[ "$branch" == lab/* ]] || die "not a lab branch: $branch"

existing="$(gh pr list --head "$branch" --state open --json url -q '.[0].url // empty')"
if [ -n "$existing" ]; then
  echo "$existing"
  exit 0
fi

git -C "$ROOT" show-ref --verify --quiet "refs/heads/$branch" || die "no local branch $branch"

IFS=/ read -r _ first second rest <<< "$branch"
body_file="$(mktemp)"
trap 'rm -f "$body_file"' EXIT

if [ "$branch" = "lab/core" ]; then
  base="main"
  title="lab: core"
  awk '/^## /{exit} {print}' "$ROOT/$LAB/README.md" > "$body_file"
elif [ "$second" = "aspect" ] && [ -z "${rest:-}" ]; then
  aspect="$first"
  base="lab/core"
  title="lab: $aspect"
  git -C "$ROOT" show "$branch:$LAB/cases/$aspect/aspect.json" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("**Question:** " + d["question"])
print()
print("**Why:** " + d["why"])
print()
print("**Source:** " + d.get("source", ""))
' > "$body_file"
else
  aspect="$first"
  case_id="$second"
  [ -z "${rest:-}" ] || die "unexpected branch shape: $branch"
  base="lab/$aspect/aspect"
  title="lab: $aspect/$case_id"
  echo "LAB explain $aspect/$case_id" > "$body_file"
fi

printf '\n%s\n' "$CLAUDE_LINE" >> "$body_file"

git -C "$ROOT" ls-remote --exit-code --heads origin "$branch" > /dev/null 2>&1 || git -C "$ROOT" push -q -u origin "$branch"
gh pr create --draft --base "$base" --head "$branch" --title "$title" --body-file "$body_file"
