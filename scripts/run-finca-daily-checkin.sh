#!/usr/bin/env bash
set -euo pipefail

PROFILE_DIR="${FINCA_PROFILE_DIR:-$HOME/.openclaw-finca}"
WORKSPACE="${FINCA_WORKSPACE:-$HOME/.openclaw/workspace-finca-ops}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
SERVER="${FINCA_TOOL_SERVER:-$WORKSPACE/tools/finca_tasks/server.py}"
ENABLED_FILE="${ENABLED_FILE:-$PROFILE_DIR/daily-checkin.enabled}"
LOG_DIR="${LOG_DIR:-/tmp/openclaw}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/finca-daily-checkin.log}"
FORCE="${1:-}"
STAMP_DIR="${STAMP_DIR:-$PROFILE_DIR/schedule-stamps}"
STAMP_FILE="$STAMP_DIR/finca-daily-checkin-$(TZ=America/Bogota date '+%Y-%m-%d').stamp"
SCHEDULED_MINUTES=$((16 * 60))
CHECKIN_TEXT="Buenas tardes. ¿En qué tareas avanzamos hoy?"

export PATH="/opt/homebrew/opt/python@3.13/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export TZ="America/Bogota"
export OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$PROFILE_DIR/openclaw.json}"
export OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-$PROFILE_DIR}"
export FINCA_WORKSPACE="$WORKSPACE"
mkdir -p "$LOG_DIR"

if [ ! -f "$ENABLED_FILE" ] && [ "$FORCE" != "--force" ]; then
  printf '%s disabled: Finca daily-checkin enable file missing\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
  exit 0
fi

if [ "$FORCE" != "--force" ]; then
  now_minutes=$((10#$(date '+%H') * 60 + 10#$(date '+%M')))
  if [ "$now_minutes" -lt "$SCHEDULED_MINUTES" ]; then
    printf '%s skipped: before 16:00 catch-up window\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 0
  fi
  if [ -f "$STAMP_FILE" ]; then
    printf '%s skipped: Finca check-in already ran today\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 0
  fi
fi

if [ ! -f "$SERVER" ]; then
  printf '%s failed: Finca tool server is missing\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
  exit 1
fi

{
  printf '\n%s Finca daily check-in start\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  printf '%s' "{\"text\":\"$CHECKIN_TEXT\"}" |
    "$PYTHON_BIN" "$SERVER" call finca_telegram_send_message
  printf '\n%s Finca daily check-in end\n' "$(date '+%Y-%m-%d %H:%M:%S')"
} >> "$LOG_FILE" 2>&1

if [ "$FORCE" != "--force" ]; then
  mkdir -p "$STAMP_DIR"
  date '+%Y-%m-%d %H:%M:%S' > "$STAMP_FILE"
fi
