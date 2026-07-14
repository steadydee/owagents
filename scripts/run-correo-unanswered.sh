#!/usr/bin/env bash
set -euo pipefail

PROFILE="${OPENCLAW_PROFILE:-owlswatch}"
OPENCLAW_BIN="${OPENCLAW_BIN:-/Users/agent/.npm-global/bin/openclaw}"
ENABLED_FILE="${ENABLED_FILE:-$HOME/.openclaw-owlswatch/email-agent.enabled}"
LOG_DIR="${LOG_DIR:-/tmp/openclaw}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/owlswatch-email-unanswered.log}"
FORCE="${1:-}"
SESSION_ID="${SESSION_ID:-correo-unanswered-$(date '+%Y%m%d-%H%M%S')}"
STAMP_DIR="${STAMP_DIR:-$HOME/.openclaw-owlswatch/schedule-stamps}"
STAMP_FILE="$STAMP_DIR/correo-unanswered-$(date '+%Y-%m-%d').stamp"
SCHEDULED_MINUTES=$((8 * 60 + 15))

export PATH="/opt/homebrew/opt/node/bin:/opt/homebrew/bin:/usr/local/bin:/Users/agent/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
mkdir -p "$LOG_DIR"

if [ "${OWLSWATCH_EMAIL_AGENT_ENABLED:-0}" != "1" ] && [ ! -f "$ENABLED_FILE" ] && [ "$FORCE" != "--force" ]; then
  printf '%s disabled: email agent enable file missing\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
  exit 0
fi

if [ "$FORCE" != "--force" ]; then
  now_minutes=$((10#$(date '+%H') * 60 + 10#$(date '+%M')))
  if [ "$now_minutes" -lt "$SCHEDULED_MINUTES" ]; then
    printf '%s skipped: before 08:15 catch-up window\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 0
  fi
  if [ -f "$STAMP_FILE" ]; then
    printf '%s skipped: unanswered scan already ran today\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 0
  fi
fi

{
  printf '\n%s unanswered scan start\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  "$OPENCLAW_BIN" --profile "$PROFILE" agent \
    --agent correo \
    --session-id "$SESSION_ID" \
    --thinking medium \
    --timeout 1200 \
    --message "Scheduled run: unanswered_7d. Scan Owl's Watch Gmail for important threads from the last 7 days where the latest meaningful message appears external and unanswered. Create/update Email Desk tasks, submit a scan summary when Operations is ready, and send a concise Telegram summary only for important unresolved items."
  printf '%s unanswered scan end\n' "$(date '+%Y-%m-%d %H:%M:%S')"
} >> "$LOG_FILE" 2>&1

if [ "$FORCE" != "--force" ]; then
  mkdir -p "$STAMP_DIR"
  date '+%Y-%m-%d %H:%M:%S' > "$STAMP_FILE"
fi
