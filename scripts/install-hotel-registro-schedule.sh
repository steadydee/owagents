#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-install}"
PLIST_DIR="$HOME/Library/LaunchAgents"
ENABLED_FILE="$HOME/.openclaw-hotel/registro-pickup.enabled"
LABEL="ai.openclaw.hotel.registro-pickup"

unload_one() {
  local label="$1"
  local plist="$PLIST_DIR/$label.plist"
  launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
  rm -f "$plist"
}

if [ "$ACTION" = "uninstall" ]; then
  unload_one "$LABEL"
  rm -f "$ENABLED_FILE"
  echo "Uninstalled Hotel Registro pickup schedule"
  exit 0
fi

if [ "$ACTION" = "disable" ]; then
  rm -f "$ENABLED_FILE"
  echo "Hotel Registro pickup schedule left installed but disabled"
  exit 0
fi

if [ "$ACTION" != "install" ]; then
  echo "Usage: $0 [install|disable|uninstall]" >&2
  exit 2
fi

mkdir -p "$PLIST_DIR" "$(dirname "$ENABLED_FILE")" /tmp/openclaw
chmod +x "$ROOT/scripts/run-hotel-registro-pickup.sh"
touch "$ENABLED_FILE"

cat > "$PLIST_DIR/$LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$ROOT/scripts/run-hotel-registro-pickup.sh</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>17</integer><key>Minute</key><integer>0</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/openclaw/hotel-registro-pickup.stdout.log</string>
  <key>StandardErrorPath</key><string>/tmp/openclaw/hotel-registro-pickup.stderr.log</string>
</dict>
</plist>
PLIST

plist="$PLIST_DIR/$LABEL.plist"
launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"

echo "Installed Hotel Registro pickup schedule"
echo "Registro pickup: 17:00 America/Bogota"
echo "Disable without uninstall: $0 disable"
