#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-install}"
LABEL="ai.openclaw.finca.daily-checkin"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LEGACY_LABEL="ai.openclaw.finca.daily-report"
LEGACY_PLIST="$HOME/Library/LaunchAgents/$LEGACY_LABEL.plist"
LEGACY_ENABLED_FILE="$HOME/.openclaw-finca/daily-report.enabled"
LEGACY_RUNNER="$HOME/.openclaw-finca/bin/run-finca-daily-report.sh"
ENABLED_FILE="$HOME/.openclaw-finca/daily-checkin.enabled"
BIN_DIR="$HOME/.openclaw-finca/bin"
RUNNER="$BIN_DIR/run-finca-daily-checkin.sh"

if [ "$ACTION" = "uninstall" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)" "$LEGACY_PLIST" 2>/dev/null || true
  rm -f "$PLIST" "$ENABLED_FILE" "$RUNNER"
  rm -f "$LEGACY_PLIST" "$LEGACY_ENABLED_FILE" "$LEGACY_RUNNER"
  echo "Uninstalled Finca daily check-in"
  exit 0
fi

if [ "$ACTION" = "disable" ]; then
  rm -f "$ENABLED_FILE"
  rm -f "$LEGACY_ENABLED_FILE"
  echo "Finca daily check-in remains installed but disabled"
  exit 0
fi

if [ "$ACTION" != "install" ]; then
  echo "Usage: $0 [install|disable|uninstall]" >&2
  exit 2
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.openclaw-finca" "$BIN_DIR" /tmp/openclaw
install -m 700 "$ROOT/scripts/run-finca-daily-checkin.sh" "$RUNNER"
touch "$ENABLED_FILE"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$RUNNER</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/openclaw/finca-daily-checkin.stdout.log</string>
  <key>StandardErrorPath</key><string>/tmp/openclaw/finca-daily-checkin.stderr.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>TZ</key><string>America/Bogota</string>
  </dict>
</dict></plist>
PLIST

launchctl bootout "gui/$(id -u)" "$LEGACY_PLIST" 2>/dev/null || true
rm -f "$LEGACY_PLIST" "$LEGACY_ENABLED_FILE" "$LEGACY_RUNNER"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed Finca daily check-in for 16:00 America/Bogota"
