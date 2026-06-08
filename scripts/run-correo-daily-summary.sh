#!/usr/bin/env bash
set -euo pipefail

PROFILE="${OPENCLAW_PROFILE:-owlswatch}"
OPENCLAW_BIN="${OPENCLAW_BIN:-/Users/agent/.npm-global/bin/openclaw}"
ENABLED_FILE="${ENABLED_FILE:-$HOME/.openclaw-owlswatch/email-agent.enabled}"
LOG_DIR="${LOG_DIR:-/tmp/openclaw}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/owlswatch-email-daily-summary.log}"
FORCE="${1:-}"
STAMP_DIR="${STAMP_DIR:-$HOME/.openclaw-owlswatch/schedule-stamps}"
STAMP_FILE="$STAMP_DIR/correo-daily-summary-$(date '+%Y-%m-%d').stamp"
SCHEDULED_MINUTES=$((8 * 60))

export PATH="/opt/homebrew/opt/node/bin:/opt/homebrew/bin:/usr/local/bin:/Users/agent/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
mkdir -p "$LOG_DIR"

if [ "${OWLSWATCH_EMAIL_AGENT_ENABLED:-0}" != "1" ] && [ ! -f "$ENABLED_FILE" ] && [ "$FORCE" != "--force" ]; then
  printf '%s disabled: email agent enable file missing\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
  exit 0
fi

if [ "$FORCE" != "--force" ]; then
  now_minutes=$((10#$(date '+%H') * 60 + 10#$(date '+%M')))
  if [ "$now_minutes" -lt "$SCHEDULED_MINUTES" ]; then
    printf '%s skipped: before 08:00 catch-up window\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 0
  fi
  if [ -f "$STAMP_FILE" ]; then
    printf '%s skipped: daily summary already ran today\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
    exit 0
  fi
fi

{
  printf '\n%s daily summary start\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  "$OPENCLAW_BIN" --profile "$PROFILE" agent \
    --agent correo \
    --session-id correo-daily-summary \
    --thinking medium \
    --timeout 1200 \
    --message "Scheduled run: daily_summary. Send one concise Telegram summary of only important Owl's Watch email whose latest external Gmail message is from the last 24 hours only. Do not include older open tasks, weekly unanswered scan results, no-reply notices, finance notifications, newsletters, promotions, spam, or resolved items. Exception: include Little Hotelier / BookingButton enquiry-received emails because they are guest inquiries, even if sent from a no-reply address. If there are no important emails from the last 24 hours, say exactly that."
  printf '%s daily summary end\n' "$(date '+%Y-%m-%d %H:%M:%S')"
} >> "$LOG_FILE" 2>&1

if [ "$FORCE" != "--force" ]; then
  mkdir -p "$STAMP_DIR"
  date '+%Y-%m-%d %H:%M:%S' > "$STAMP_FILE"
fi
