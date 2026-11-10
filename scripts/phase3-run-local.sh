#!/usr/bin/env bash
# Runs the Phase 3 scan on this PC: pulls the shared scan state, scans, and pushes it back.
# The tokens come from a file outside the project, so they never enter git.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
. "$HOME/.config/airtel-phase3/globalping_tokens.env"
set +a
mkdir -p output
.venv/bin/python3 -u -m phase3 sync run --owner local --workers 12 2>&1 | tee output/phase3_console.log
