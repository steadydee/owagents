#!/usr/bin/env bash
set -euo pipefail

PROFILE="${OPENCLAW_PROFILE:-finca}"
OPENCLAW_BIN="${OPENCLAW_BIN:-/Users/agent/.npm-global/bin/openclaw}"
ENABLED_FILE="${ENABLED_FILE:-$HOME/.openclaw-finca/daily-report.enabled}"
LOG_DIR="${LOG_DIR:-/tmp/openclaw}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/finca-daily-report.log}"
FORCE="${1:-}"
STAMP_DIR="${STAMP_DIR:-$HOME/.openclaw-finca/schedule-stamps}"
STAMP_FILE="$STAMP_DIR/finca-daily-report-$(TZ=America/Bogota date '+%Y-%m-%d').stamp"
SCHEDULED_MINUTES=$((7 * 60))

export PATH="/opt/homebrew/opt/node/bin:/opt/homebrew/bin:/usr/local/bin:/Users/agent/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
export TZ="America/Bogota"
mkdir -p "$LOG_DIR"

if [ ! -f "$ENABLED_FILE" ] && [ "$FORCE" != "--force" ]; then
  printf '%s disabled: Finca daily-report enable file missing\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
  exit 0
fi

if [ "$FORCE" != "--force" ]; then
  now_minutes=$((10#$(date '+%H') * 60 + 10#$(date '+%M')))
  if [ "$now_minutes" -lt "$SCHEDULED_MINUTES" ]; then
    printf '%s skipped: before 07:00 catch-up window\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 0
  fi
  if [ -f "$STAMP_FILE" ]; then
    printf '%s skipped: Finca report already ran today\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 0
  fi
fi

{
  printf '\n%s Finca daily report start\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  "$OPENCLAW_BIN" --profile "$PROFILE" agent \
    --agent finca \
    --session-id finca-daily-report \
    --thinking low \
    --timeout 600 \
    --message "Scheduled run: finca_daily_report. Call finca_tasks_send_daily_report. Do not compose or send a second report."
  printf '%s Finca daily report end\n' "$(date '+%Y-%m-%d %H:%M:%S')"
} >> "$LOG_FILE" 2>&1

if [ "$FORCE" != "--force" ]; then
  mkdir -p "$STAMP_DIR"
  date '+%Y-%m-%d %H:%M:%S' > "$STAMP_FILE"
fi
