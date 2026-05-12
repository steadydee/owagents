#!/usr/bin/env bash
set -euo pipefail

PROFILE="${OPENCLAW_PROFILE:-owlswatch}"
OPENCLAW_BIN="${OPENCLAW_BIN:-/Users/agent/.npm-global/bin/openclaw}"
export PATH="/opt/homebrew/opt/node/bin:/opt/homebrew/bin:/usr/local/bin:/Users/agent/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
LOG_DIR="${LOG_DIR:-/tmp/openclaw}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/owlswatch-telegram-watchdog.log}"
LOCK_DIR="${LOCK_DIR:-/tmp/owlswatch-telegram-watchdog.lock}"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-600}"
STAMP_FILE="${STAMP_FILE:-/tmp/owlswatch-telegram-watchdog.last-restart}"

mkdir -p "$LOG_DIR"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  log "another watchdog run is still active; skipping"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

if [ ! -x "$OPENCLAW_BIN" ]; then
  log "openclaw binary not found or not executable: $OPENCLAW_BIN"
  exit 1
fi

status_output="$("$OPENCLAW_BIN" --profile "$PROFILE" channels status --probe 2>&1 || true)"

if printf '%s\n' "$status_output" | grep -Eq 'Telegram .*: .*running.*connected'; then
  log "ok: Telegram running and connected"
  exit 0
fi

now="$(date +%s)"
last_restart=0
if [ -f "$STAMP_FILE" ]; then
  last_restart="$(cat "$STAMP_FILE" 2>/dev/null || echo 0)"
fi

if [ "$((now - last_restart))" -lt "$COOLDOWN_SECONDS" ]; then
  log "unhealthy but cooldown active; skipping restart"
  printf '%s\n' "$status_output" | sed 's/^/  /' >> "$LOG_FILE"
  exit 0
fi

log "unhealthy Telegram channel; restarting owlswatch gateway"
printf '%s\n' "$status_output" | sed 's/^/  /' >> "$LOG_FILE"

if "$OPENCLAW_BIN" --profile "$PROFILE" gateway restart >> "$LOG_FILE" 2>&1; then
  date +%s > "$STAMP_FILE"
  sleep 8
  after_output="$("$OPENCLAW_BIN" --profile "$PROFILE" channels status --probe 2>&1 || true)"
  if printf '%s\n' "$after_output" | grep -Eq 'Telegram .*: .*running.*connected'; then
    log "recovered: Telegram running and connected after restart"
    exit 0
  fi
  log "restart completed, but Telegram is still unhealthy"
  printf '%s\n' "$after_output" | sed 's/^/  /' >> "$LOG_FILE"
  exit 2
fi

log "gateway restart command failed"
exit 1
