#!/usr/bin/env bash
# Merge lab/core into every lab/<aspect>/aspect, each aspect into its case branches that have no
# results/verdict.json yet, and push. Flags:
#   --deploy     also merge lab/core into lab/deploy
#   --integrate  rebuild local lab/all from lab/core with every aspect and case merged in; pushed, no PR
# Stops on the first merge conflict, names the branch, and leaves the tree clean.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
LAB="server/chat/V2/personalization/lab"

die() { echo "lab_sync: $*" >&2; exit 1; }

deploy=0; integrate=0
for arg in "$@"; do
  case "$arg" in
    --deploy) deploy=1 ;;
    --integrate) integrate=1 ;;
    *) die "unknown argument: $arg" ;;
  esac
done

[ -z "$(git -C "$ROOT" status --porcelain)" ] || die "working tree is dirty; commit or stash first"

orig="$(git -C "$ROOT" branch --show-current)"
git -C "$ROOT" fetch -q origin

# merge <target-branch> <source-ref>: checks out the target, merges, stops on conflict
merge_into() {
  local target="$1" source="$2"
  git -C "$ROOT" checkout -q "$target"
  if ! git -C "$ROOT" merge -q --no-edit "$source" > /dev/null 2>&1; then
    git -C "$ROOT" merge --abort 2>/dev/null || true
    git -C "$ROOT" checkout -q "$orig"
    die "merge conflict: $source into $target (resolve by hand; nothing further was pushed)"
  fi
}

push_branch() { git -C "$ROOT" push -q origin "$1"; }

core_ref="origin/lab/core"
git -C "$ROOT" show-ref --verify --quiet "refs/remotes/$core_ref" || die "no $core_ref"

aspects="$(git -C "$ROOT" for-each-ref --format='%(refname:short)' 'refs/heads/lab/*/aspect')"
for a in $aspects; do
  aspect="$(echo "$a" | cut -d/ -f2)"
  git -C "$ROOT" show-ref --verify --quiet "refs/remotes/origin/$a" && merge_into "$a" "origin/$a" || true
  merge_into "$a" "$core_ref"
  push_branch "$a"
  echo "synced $a"
done

cases="$(git -C "$ROOT" for-each-ref --format='%(refname:short)' 'refs/heads/lab/*/*' \
  | grep -v '/aspect$' || true)"
for c in $cases; do
  aspect="$(echo "$c" | cut -d/ -f2)"
  case_id="$(echo "$c" | cut -d/ -f3)"
  verdict="$LAB/cases/$aspect/$case_id/results/verdict.json"
  if git -C "$ROOT" cat-file -e "$c:$verdict" 2>/dev/null; then
    echo "skipped $c (has a verdict)"
    continue
  fi
  merge_into "$c" "lab/$aspect/aspect"
  push_branch "$c"
  echo "synced $c"
done

if [ "$deploy" -eq 1 ]; then
  git -C "$ROOT" show-ref --verify --quiet refs/heads/lab/deploy || git -C "$ROOT" checkout -q -b lab/deploy "$core_ref"
  merge_into lab/deploy "$core_ref"
  push_branch lab/deploy
  echo "synced lab/deploy"
fi

if [ "$integrate" -eq 1 ]; then
  git -C "$ROOT" checkout -q -B lab/all "$core_ref"
  for a in $aspects; do merge_into lab/all "$a"; done
  for c in $cases; do merge_into lab/all "$c"; done
  git -C "$ROOT" push -q --force-with-lease -u origin lab/all
  echo "built lab/all (rebuilt from lab/core; pushed; no PR)"
fi

git -C "$ROOT" checkout -q "$orig"
echo "done; back on $orig"
