#!/usr/bin/env bash
set -euo pipefail

PROFILE="${OPENCLAW_PROFILE:-hotel}"
OPENCLAW_BIN="${OPENCLAW_BIN:-/Users/agent/.npm-global/bin/openclaw}"
ENABLED_FILE="${ENABLED_FILE:-$HOME/.openclaw-hotel/hotel-summary.enabled}"
LOG_DIR="${LOG_DIR:-/tmp/openclaw}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/hotel-daily-summary.log}"
FORCE="${1:-}"
STAMP_DIR="${STAMP_DIR:-$HOME/.openclaw-hotel/schedule-stamps}"
STAMP_FILE="$STAMP_DIR/hotel-daily-summary-$(date '+%Y-%m-%d').stamp"
SCHEDULED_MINUTES=$((16 * 60))

export PATH="/opt/homebrew/opt/node/bin:/opt/homebrew/bin:/usr/local/bin:/Users/agent/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
mkdir -p "$LOG_DIR"

if [ "${HOTEL_DAILY_SUMMARY_ENABLED:-0}" != "1" ] && [ ! -f "$ENABLED_FILE" ] && [ "$FORCE" != "--force" ]; then
  printf '%s disabled: hotel summary enable file missing\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
  exit 0
fi

if [ "$FORCE" != "--force" ]; then
  now_minutes=$((10#$(date '+%H') * 60 + 10#$(date '+%M')))
  if [ "$now_minutes" -lt "$SCHEDULED_MINUTES" ]; then
    printf '%s skipped: before 16:00 catch-up window\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 0
  fi
  if [ -f "$STAMP_FILE" ]; then
    printf '%s skipped: hotel summary already ran today\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 0
  fi
fi

{
  printf '\n%s hotel daily summary start\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  "$OPENCLAW_BIN" --profile "$PROFILE" agent \
    --agent hotel \
    --session-id hotel-daily-summary \
    --thinking medium \
    --timeout 1200 \
    --message "Scheduled run: tomorrow_summary. Send the Owl's Watch staff Telegram hotel summary for tomorrow in Spanish. Include who arrives, who checks out, who stays another day, bird tours, pasadias/day visits, and concise operational notes. Do not include prices, rates, balances, deposits, payment status, payment notes, or other finance details. Use the PMS tools as current truth and send via hotel_telegram_send_message."
  printf '%s hotel daily summary end\n' "$(date '+%Y-%m-%d %H:%M:%S')"
} >> "$LOG_FILE" 2>&1

if [ "$FORCE" != "--force" ]; then
  mkdir -p "$STAMP_DIR"
  date '+%Y-%m-%d %H:%M:%S' > "$STAMP_FILE"
fi
