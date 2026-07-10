#!/usr/bin/env bash
set -euo pipefail

PROFILE="${OPENCLAW_PROFILE:-finca}"
OPENCLAW_BIN="${OPENCLAW_BIN:-/Users/agent/.npm-global/bin/openclaw}"
LOG_DIR="${LOG_DIR:-/tmp/openclaw}"
LOG_FILE="$LOG_DIR/finca-telegram-watchdog.log"
LOCK_DIR="/tmp/finca-telegram-watchdog.lock"
STAMP_FILE="/tmp/finca-telegram-watchdog.last-restart"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-600}"

export PATH="/opt/homebrew/opt/node/bin:/opt/homebrew/bin:/usr/local/bin:/Users/agent/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
mkdir -p "$LOG_DIR"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"; }
if ! mkdir "$LOCK_DIR" 2>/dev/null; then exit 0; fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

status_output="$($OPENCLAW_BIN --profile "$PROFILE" channels status --probe 2>&1 || true)"
if printf '%s\n' "$status_output" | grep -Eq 'Telegram .*: .*enabled, configured, running' \
  && printf '%s\n' "$status_output" | grep -Eq 'works, audit ok'; then
  log "ok: Telegram operational"
  exit 0
fi

now="$(date +%s)"
last_restart="$(cat "$STAMP_FILE" 2>/dev/null || echo 0)"
case "$last_restart" in ''|*[!0-9]*) last_restart=0 ;; esac
if [ "$((now - last_restart))" -lt "$COOLDOWN_SECONDS" ]; then
  log "unhealthy but cooldown active"
  exit 0
fi

log "unhealthy Telegram channel; restarting Finca gateway"
printf '%s\n' "$status_output" | sed 's/^/  /' >> "$LOG_FILE"
if "$OPENCLAW_BIN" --profile "$PROFILE" gateway restart >> "$LOG_FILE" 2>&1; then
  date +%s > "$STAMP_FILE"
  sleep 8
  after="$($OPENCLAW_BIN --profile "$PROFILE" channels status --probe 2>&1 || true)"
  if printf '%s\n' "$after" | grep -Eq 'Telegram .*: .*enabled, configured, running' \
    && printf '%s\n' "$after" | grep -Eq 'works, audit ok'; then
    log "recovered after restart"
    exit 0
  fi
fi
log "Finca Telegram remains unhealthy"
exit 1
