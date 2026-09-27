#!/usr/bin/env bash
set -euo pipefail

PROFILE_DIR="${FINCA_PROFILE_DIR:-$HOME/.openclaw-finca}"
WORKSPACE="${FINCA_WORKSPACE:-$HOME/.openclaw/workspace-finca-ops}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
RUNNER="${FINCA_SCHEDULE_RUNNER:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run-finca-tool-job.py}"
LOG_DIR="${LOG_DIR:-/tmp/openclaw}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/finca-daily-report.log}"

export PATH="/opt/homebrew/opt/python@3.13/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export TZ="America/Bogota"
export FINCA_PROFILE_DIR="$PROFILE_DIR"
export FINCA_WORKSPACE="$WORKSPACE"
export OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$PROFILE_DIR/openclaw.json}"
export OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-$PROFILE_DIR}"
mkdir -p "$LOG_DIR"

# The deterministic runner owns due checks, per-job locking, delivery checkpoints
# and the legacy daily stamp. It logs only fixed outcome codes and counts.
"$PYTHON_BIN" "$RUNNER" report "$@" >> "$LOG_FILE" 2>&1
