#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-install}"
PLIST_DIR="$HOME/Library/LaunchAgents"
ENABLED_FILE="$HOME/.openclaw-hotel/hotel-summary.enabled"
DAILY_LABEL="ai.openclaw.hotel.daily-summary"

unload_one() {
  local label="$1"
  local plist="$PLIST_DIR/$label.plist"
  launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
  rm -f "$plist"
}

if [ "$ACTION" = "uninstall" ]; then
  unload_one "$DAILY_LABEL"
  rm -f "$ENABLED_FILE"
  echo "Uninstalled Hotel daily summary schedule"
  exit 0
fi

if [ "$ACTION" = "disable" ]; then
  rm -f "$ENABLED_FILE"
  echo "Hotel daily summary schedule left installed but disabled"
  exit 0
fi

if [ "$ACTION" != "install" ]; then
  echo "Usage: $0 [install|disable|uninstall]" >&2
  exit 2
fi

mkdir -p "$PLIST_DIR" "$(dirname "$ENABLED_FILE")" /tmp/openclaw
chmod +x "$ROOT/scripts/run-hotel-daily-summary.sh"
touch "$ENABLED_FILE"

cat > "$PLIST_DIR/$DAILY_LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$DAILY_LABEL</string>
  <key>ProgramArguments</key><array><string>$ROOT/scripts/run-hotel-daily-summary.sh</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/openclaw/hotel-daily-summary.stdout.log</string>
  <key>StandardErrorPath</key><string>/tmp/openclaw/hotel-daily-summary.stderr.log</string>
</dict>
</plist>
PLIST

plist="$PLIST_DIR/$DAILY_LABEL.plist"
launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"

echo "Installed Hotel daily summary schedule"
echo "Daily summary: 16:00 America/Bogota"
echo "Disable without uninstall: $0 disable"
