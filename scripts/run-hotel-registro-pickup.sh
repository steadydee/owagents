#!/usr/bin/env bash
set -euo pipefail

PROFILE="${OPENCLAW_PROFILE:-hotel}"
OPENCLAW_BIN="${OPENCLAW_BIN:-/Users/agent/.npm-global/bin/openclaw}"
ENABLED_FILE="${ENABLED_FILE:-$HOME/.openclaw-hotel/registro-pickup.enabled}"
LOG_DIR="${LOG_DIR:-/tmp/openclaw}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/hotel-registro-pickup.log}"
FORCE="${1:-}"
STAMP_DIR="${STAMP_DIR:-$HOME/.openclaw-hotel/schedule-stamps}"
STAMP_FILE="$STAMP_DIR/hotel-registro-pickup-$(date '+%Y-%m-%d').stamp"
SCHEDULED_MINUTES=$((17 * 60))

export PATH="/opt/homebrew/opt/node/bin:/opt/homebrew/bin:/usr/local/bin:/Users/agent/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
mkdir -p "$LOG_DIR"

if [ "${HOTEL_REGISTRO_PICKUP_ENABLED:-0}" != "1" ] && [ ! -f "$ENABLED_FILE" ] && [ "$FORCE" != "--force" ]; then
  printf '%s disabled: registro pickup enable file missing\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
  exit 0
fi

if [ "$FORCE" != "--force" ]; then
  now_minutes=$((10#$(date '+%H') * 60 + 10#$(date '+%M')))
  if [ "$now_minutes" -lt "$SCHEDULED_MINUTES" ]; then
    printf '%s skipped: before 17:00 catch-up window\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 0
  fi
  if [ -f "$STAMP_FILE" ]; then
    printf '%s skipped: registro pickup already ran today\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 0
  fi
fi

{
  printf '\n%s hotel registro pickup start\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  "$OPENCLAW_BIN" --profile "$PROFILE" agent \
    --agent hotel \
    --session-id hotel-registro-pickup \
    --thinking medium \
    --timeout 1800 \
    --message "Scheduled run: registro_daily_pickup. Call hotel_registro_daily_pickup with submitTra=true, notify=true, maxRecords=25, daysBack=7, and daysAhead=2. This should catch documents uploaded after checkout, extract uploaded Registro documents, and submit TRA/SIRE only when ready and only through the receipt-gated configured submitter. The tool must keep Telegram silent for successful, skipped, already-complete, and empty runs; it sends one staff-safe Spanish alert only when needsReview or errors is non-empty. Do not send an additional Telegram message. Do not include passport numbers, document numbers, birth dates, raw OCR, document URLs, prices, balances, deposits, or payment details."
  printf '%s hotel registro pickup end\n' "$(date '+%Y-%m-%d %H:%M:%S')"
} >> "$LOG_FILE" 2>&1

if [ "$FORCE" != "--force" ]; then
  mkdir -p "$STAMP_DIR"
  date '+%Y-%m-%d %H:%M:%S' > "$STAMP_FILE"
fi
