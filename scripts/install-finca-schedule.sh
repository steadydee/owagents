#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-install}"
LABEL="ai.openclaw.finca.daily-report"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ENABLED_FILE="$HOME/.openclaw-finca/daily-report.enabled"

if [ "$ACTION" = "uninstall" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  rm -f "$PLIST" "$ENABLED_FILE"
  echo "Uninstalled Finca daily report"
  exit 0
fi

if [ "$ACTION" = "disable" ]; then
  rm -f "$ENABLED_FILE"
  echo "Finca daily report remains installed but disabled"
  exit 0
fi

if [ "$ACTION" != "install" ]; then
  echo "Usage: $0 [install|disable|uninstall]" >&2
  exit 2
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.openclaw-finca" /tmp/openclaw
chmod +x "$ROOT/scripts/run-finca-daily-report.sh"
touch "$ENABLED_FILE"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$ROOT/scripts/run-finca-daily-report.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/openclaw/finca-daily-report.stdout.log</string>
  <key>StandardErrorPath</key><string>/tmp/openclaw/finca-daily-report.stderr.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>TZ</key><string>America/Bogota</string>
  </dict>
</dict></plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed Finca daily report for 07:00 America/Bogota"
