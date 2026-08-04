#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-install}"
PROFILE_DIR="${HOTEL_PROFILE_DIR:-$HOME/.openclaw-hotel}"
PLIST_DIR="$HOME/Library/LaunchAgents"
LABEL="ai.openclaw.hotel.telegram-observer"
PLIST="$PLIST_DIR/$LABEL.plist"
RUNTIME_SCRIPT="$PROFILE_DIR/monitor/observe-telegram-channel.py"
OPENCLAW_BIN="${OPENCLAW_BIN:-$(command -v openclaw)}"
GATEWAY_PLIST="$PLIST_DIR/ai.openclaw.hotel.plist"

unload() {
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
}

if [ "$ACTION" = "uninstall" ]; then
  unload
  rm -f "$PLIST" "$RUNTIME_SCRIPT"
  echo "Uninstalled Hotel Telegram observer"
  exit 0
fi

if [ "$ACTION" != "install" ]; then
  echo "Usage: $0 [install|uninstall]" >&2
  exit 2
fi

mkdir -p "$PROFILE_DIR/monitor" "$PROFILE_DIR/logs" "$PLIST_DIR"
install -m 700 "$ROOT/scripts/observe-telegram-channel.py" "$RUNTIME_SCRIPT"

# OpenClaw's installer historically sent Hotel gateway stderr to /dev/null.
# Preserve it for incident diagnosis without changing the gateway owner.
if [ -f "$GATEWAY_PLIST" ]; then
  /usr/libexec/PlistBuddy -c "Set :StandardErrorPath $PROFILE_DIR/logs/gateway.error.log" "$GATEWAY_PLIST"
  touch "$PROFILE_DIR/logs/gateway.error.log"
  chmod 600 "$PROFILE_DIR/logs/gateway.error.log"
fi

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$RUNTIME_SCRIPT</string>
    <string>--profile</string><string>hotel</string>
    <string>--state-dir</string><string>$PROFILE_DIR</string>
    <string>--openclaw-bin</string><string>$OPENCLAW_BIN</string>
  </array>
  <key>StartInterval</key><integer>120</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/Users/agent/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StandardOutPath</key><string>$PROFILE_DIR/logs/telegram-observer.stdout.log</string>
  <key>StandardErrorPath</key><string>$PROFILE_DIR/logs/telegram-observer.stderr.log</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST" >/dev/null
unload
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Installed Hotel Telegram observer (read-only, every 120 seconds)"
echo "Health journal: $PROFILE_DIR/logs/telegram-health.jsonl"
echo "Ingress journal: $PROFILE_DIR/logs/telegram-ingress-journal.jsonl"
echo "Gateway stderr: $PROFILE_DIR/logs/gateway.error.log"
