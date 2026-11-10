#!/usr/bin/env bash
# Record a case: regenerate the tree, render the pages, publish the wiki, read the page back.
# Usage: scripts/lab_record.sh <aspect>/<case>
# Exit: 0 all steps done; 1 a step failed; 2 bad usage; 3 the wiki has unrelated changes.
set -uo pipefail
CASE_ID="${1:-}"
[[ "$CASE_ID" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "usage: lab_record.sh <aspect>/<case>" >&2; exit 2; }
ASPECT="${CASE_ID%%/*}"
W="${W:-$(cd "$(dirname "$0")/.." && pwd)}"
WIKI="${WIKI:-$HOME/dev/sefaria/sefaria-wiki}"
LAB_WIKI_DIR="$WIKI/wiki/projects/la-personalization/lab"
OUT="$W/.lab-out"
PLAN="wiki/meta/plan-2026-11-10-la-personalization-lab.md"

# 1. the wiki may hold changes only inside this project (and the plan)
stray=()
while IFS= read -r line; do
  path="${line:3}"; path="${path##* -> }"; path="${path%\"}"; path="${path#\"}"
  case "$path" in
    wiki/projects/la-personalization/*|"$PLAN") ;;
    *) stray+=("$path") ;;
  esac
done < <(git -C "$WIKI" status --porcelain --untracked-files=all -- wiki .raw)
if ((${#stray[@]})); then
  echo "refusing: the wiki has changes outside wiki/projects/la-personalization/:" >&2
  printf '  %s\n' "${stray[@]}" >&2
  exit 3
fi

# 2. the tree, with pull request links
mkdir -p "$OUT"
"$W"/scripts/lab.sh tree --remote origin --out "$OUT/tree.json" --with-prs || exit 1

# 3. render: HTML to the worktree, pages to the wiki
"$W"/scripts/lab.sh render --tree "$OUT/tree.json" --html "$OUT/tree.html" --wiki-dir "$LAB_WIKI_DIR" || exit 1

# 4. publish
(cd "$WIKI" && bash scripts/wiki-sync.sh publish) || { echo "wiki publish failed" >&2; exit 1; }

# 5. read back: the published page carries the case's current sha
SHA="$(python3 - "$OUT/tree.json" "$CASE_ID" <<'PY'
import json, sys
tree = json.load(open(sys.argv[1]))
for aspect in tree["aspects"]:
    for case in aspect["cases"]:
        if case["id"] == sys.argv[2]:
            print(case.get("case_sha", ""))
PY
)"
[[ -n "$SHA" ]] || { echo "case $CASE_ID is not in the tree (is its branch pushed?)" >&2; exit 1; }
git -C "$WIKI" fetch -q sefaria || { echo "fetch of the wiki remote failed" >&2; exit 1; }
if git -C "$WIKI" show "sefaria/main:wiki/projects/la-personalization/lab/$ASPECT.md" | grep -qxF "case_sha: $SHA"; then
  echo "recorded $CASE_ID (case_sha $SHA); tree at $OUT/tree.html"
else
  echo "read-back failed: sefaria/main:.../lab/$ASPECT.md has no 'case_sha: $SHA'" >&2
  exit 1
fi
