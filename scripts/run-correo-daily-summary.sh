#!/usr/bin/env bash
set -euo pipefail

PROFILE="${OPENCLAW_PROFILE:-owlswatch}"
OPENCLAW_BIN="${OPENCLAW_BIN:-/Users/agent/.npm-global/bin/openclaw}"
ENABLED_FILE="${ENABLED_FILE:-$HOME/.openclaw-owlswatch/email-agent.enabled}"
LOG_DIR="${LOG_DIR:-/tmp/openclaw}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/owlswatch-email-daily-summary.log}"
FORCE="${1:-}"

export PATH="/opt/homebrew/opt/node/bin:/opt/homebrew/bin:/usr/local/bin:/Users/agent/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
mkdir -p "$LOG_DIR"

if [ "${OWLSWATCH_EMAIL_AGENT_ENABLED:-0}" != "1" ] && [ ! -f "$ENABLED_FILE" ] && [ "$FORCE" != "--force" ]; then
  printf '%s disabled: email agent enable file missing\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
  exit 0
fi

{
  printf '\n%s daily summary start\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  "$OPENCLAW_BIN" --profile "$PROFILE" agent \
    --agent correo \
    --session-id correo-daily-summary \
    --thinking medium \
    --timeout 1200 \
    --message "Scheduled run: daily_summary. Send one concise Telegram summary of only important Owl's Watch email tasks and important new email from the last 24 hours. Include drafts ready, human-needed items, payment/quote/complaint/operator items, and unanswered important threads. Exclude newsletters, promotions, spam, and resolved items."
  printf '%s daily summary end\n' "$(date '+%Y-%m-%d %H:%M:%S')"
} >> "$LOG_FILE" 2>&1
