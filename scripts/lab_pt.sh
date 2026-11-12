#!/usr/bin/env bash
# pytest on local Postgres from the worktree's server dir; prints the last line, exits with pytest's code.
W="${W:-$(cd "$(dirname "$0")/.." && pwd)}"
log="$(mktemp -t lab-pt.XXXXXX)"
trap 'rm -f "$log"' EXIT
cd "$W"/server && ./.venv/bin/pytest --ds=chatbot_server.test_settings_postgres -q "$@" > "$log" 2>&1; rc=$?
# the pytest summary line (not interpreter exit noise printed after it)
grep -E "^=+ .* in [0-9.]+s" "$log" | tail -1 || tail -1 "$log"
exit $rc
