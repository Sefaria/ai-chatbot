#!/usr/bin/env bash
# pytest on local Postgres from the worktree's server dir; prints the last line, exits with pytest's code.
W="${W:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$W"/server && ./.venv/bin/pytest --ds=chatbot_server.test_settings_postgres -q "$@" > /tmp/lab-pt.log 2>&1; rc=$?
# the pytest summary line (not interpreter exit noise printed after it)
grep -E "^=+ .* in [0-9.]+s" /tmp/lab-pt.log | tail -1 || tail -1 /tmp/lab-pt.log
exit $rc
