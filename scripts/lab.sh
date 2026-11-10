#!/usr/bin/env bash
W="${W:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$W"/server && exec ./.venv/bin/python -m chat.V2.personalization.lab "$@"
