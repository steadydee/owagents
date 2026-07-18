#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

FAKE_BIN="$TMP_DIR/openclaw"
FAKE_STATE_DIR="$TMP_DIR/fake"
mkdir -p "$FAKE_STATE_DIR" "$TMP_DIR/profile/logs"

cat > "$FAKE_BIN" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_STATE_DIR/calls"
if [ "$1" != "--profile" ] || [ "$2" != "hotel" ]; then
  exit 9
fi
if [ "$3" = "gateway" ] && [ "$4" = "restart" ]; then
  touch "$FAKE_STATE_DIR/restarted"
  exit 0
fi
if [ "$3" = "channels" ] && [ "$4" = "status" ] && [ "$5" = "--probe" ]; then
  if [ -f "$FAKE_STATE_DIR/restarted" ]; then
    echo "Telegram default: enabled, configured, running, connected, works, audit ok"
    exit 0
  fi
  echo "Gateway unreachable"
  exit 1
fi
exit 8
SH
chmod 700 "$FAKE_BIN"
export FAKE_STATE_DIR

run_watchdog() {
  OPENCLAW_PROFILE=hotel \
  OPENCLAW_BIN="$FAKE_BIN" \
  OPENCLAW_STATE_DIR="$TMP_DIR/profile" \
  LOG_FILE="$TMP_DIR/watchdog.log" \
  LOCK_DIR="$TMP_DIR/watchdog.lock" \
  STAMP_FILE="$TMP_DIR/restart.stamp" \
  ERROR_CURSOR_FILE="$TMP_DIR/error.cursor" \
  COOLDOWN_SECONDS=0 \
  RESTART_SETTLE_SECONDS=0 \
  STALE_SPOOL_SECONDS="${STALE_SPOOL_SECONDS:-180}" \
    "$ROOT/scripts/watchdog-owlswatch-telegram.sh"
}

run_watchdog
grep -q -- '--profile hotel gateway restart' "$FAKE_STATE_DIR/calls"
grep -q 'recovered: Telegram operational after restart' "$TMP_DIR/watchdog.log"

: > "$FAKE_STATE_DIR/calls"
: > "$TMP_DIR/watchdog.log"
rm -f "$FAKE_STATE_DIR/restarted"
mkdir -p "$TMP_DIR/profile/telegram/ingress-spool-default"
printf '{}\n' > "$TMP_DIR/profile/telegram/ingress-spool-default/123.json"
touch "$FAKE_STATE_DIR/force-healthy"
sed -i '' '/if \[ -f "$FAKE_STATE_DIR\/restarted" \]; then/i\
  if [ -f "$FAKE_STATE_DIR/force-healthy" ]; then echo "Telegram default: enabled, configured, running, connected, works, audit ok"; exit 0; fi
' "$FAKE_BIN"
STALE_SPOOL_SECONDS=0 run_watchdog
grep -q -- '--profile hotel gateway restart' "$FAKE_STATE_DIR/calls"
grep -q 'stale update' "$TMP_DIR/watchdog.log"

: > "$FAKE_STATE_DIR/calls"
: > "$TMP_DIR/watchdog.log"
rm -f "$TMP_DIR/profile/telegram/ingress-spool-default/123.json"
run_watchdog
if grep -q 'gateway restart' "$FAKE_STATE_DIR/calls"; then
  echo "Healthy watchdog run unexpectedly restarted the gateway" >&2
  exit 1
fi
grep -q 'ok: Telegram operational' "$TMP_DIR/watchdog.log"

echo "Telegram watchdog tests passed."
