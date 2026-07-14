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
OPENCLAW_LOG_FILE="${OPENCLAW_LOG_FILE:-$LOG_DIR/openclaw-$(date '+%Y-%m-%d').log}"
ERROR_CURSOR_FILE="${ERROR_CURSOR_FILE:-/tmp/owlswatch-telegram-watchdog.log-cursor}"
STATE_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw-$PROFILE}"
INGRESS_SPOOL_DIR="${INGRESS_SPOOL_DIR:-$STATE_DIR/telegram/ingress-spool-default}"
STALE_SPOOL_SECONDS="${STALE_SPOOL_SECONDS:-180}"

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

status_failed=0
status_output="$("$OPENCLAW_BIN" --profile "$PROFILE" channels status --probe 2>&1)" || status_failed=1

bot_init_error=0
if [ -f "$OPENCLAW_LOG_FILE" ]; then
  current_size="$(wc -c < "$OPENCLAW_LOG_FILE" | tr -d ' ')"
  last_size=0
  if [ -f "$ERROR_CURSOR_FILE" ]; then
    last_size="$(cat "$ERROR_CURSOR_FILE" 2>/dev/null || echo 0)"
  fi
  case "$last_size" in
    ''|*[!0-9]*) last_size=0 ;;
  esac
  if [ "$current_size" -lt "$last_size" ]; then
    last_size=0
  fi
  if [ "$current_size" -gt "$last_size" ]; then
    delta="$((current_size - last_size))"
    if tail -c "$delta" "$OPENCLAW_LOG_FILE" 2>/dev/null | grep -q 'Bot not initialized'; then
      bot_init_error=1
    fi
    printf '%s\n' "$current_size" > "$ERROR_CURSOR_FILE"
  fi
fi

stale_spool=0
stale_spool_count=0
oldest_spool_age=0
if [ -d "$INGRESS_SPOOL_DIR" ]; then
  now_for_spool="$(date +%s)"
  while IFS= read -r spool_file; do
    [ -n "$spool_file" ] || continue
    mtime="$(stat -f '%m' "$spool_file" 2>/dev/null || echo "$now_for_spool")"
    case "$mtime" in
      ''|*[!0-9]*) mtime="$now_for_spool" ;;
    esac
    age="$((now_for_spool - mtime))"
    if [ "$age" -gt "$oldest_spool_age" ]; then
      oldest_spool_age="$age"
    fi
    if [ "$age" -ge "$STALE_SPOOL_SECONDS" ]; then
      stale_spool=1
      stale_spool_count="$((stale_spool_count + 1))"
    fi
  done < <(find "$INGRESS_SPOOL_DIR" -maxdepth 1 -type f -name '*.json' -print 2>/dev/null)
fi

telegram_operational=0
if [ "$status_failed" -eq 0 ] \
  && printf '%s\n' "$status_output" | grep -Eq 'Telegram .*: .*enabled, configured, running' \
  && printf '%s\n' "$status_output" | grep -Eq 'works, audit ok'; then
  telegram_operational=1
fi

# OpenClaw may report Telegram as disconnected when polling is idle. That is
# fine for low-volume bots; restart only when the probe fails or the handler
# emits a known hard failure.
if [ "$bot_init_error" -eq 0 ] && [ "$stale_spool" -eq 0 ] && [ "$telegram_operational" -eq 1 ]; then
  log "ok: Telegram operational"
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

if [ "$bot_init_error" -eq 1 ]; then
  log "unhealthy Telegram handler: Bot not initialized error detected; restarting owlswatch gateway"
elif [ "$stale_spool" -eq 1 ]; then
  log "unhealthy Telegram ingress spool: $stale_spool_count stale update(s), oldest age ${oldest_spool_age}s; restarting owlswatch gateway"
elif [ "$status_failed" -ne 0 ]; then
  log "unhealthy Telegram channel: status probe failed; restarting owlswatch gateway"
else
  log "unhealthy Telegram channel; restarting owlswatch gateway"
fi
printf '%s\n' "$status_output" | sed 's/^/  /' >> "$LOG_FILE"

if "$OPENCLAW_BIN" --profile "$PROFILE" gateway restart >> "$LOG_FILE" 2>&1; then
  date +%s > "$STAMP_FILE"
  sleep 8
  after_output="$("$OPENCLAW_BIN" --profile "$PROFILE" channels status --probe 2>&1 || true)"
  if printf '%s\n' "$after_output" | grep -Eq 'Telegram .*: .*enabled, configured, running' \
    && printf '%s\n' "$after_output" | grep -Eq 'works, audit ok'; then
    log "recovered: Telegram operational after restart"
    exit 0
  fi
  log "restart completed, but Telegram is still unhealthy"
  printf '%s\n' "$after_output" | sed 's/^/  /' >> "$LOG_FILE"
  exit 2
fi

log "gateway restart command failed"
exit 1
