#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-install}"
BIN_DIR="$HOME/.openclaw-finca/bin"
CHECKIN_LABEL="ai.openclaw.finca.daily-checkin"
CHECKIN_PLIST="$HOME/Library/LaunchAgents/$CHECKIN_LABEL.plist"
CHECKIN_ENABLED_FILE="$HOME/.openclaw-finca/daily-checkin.enabled"
CHECKIN_RUNNER="$BIN_DIR/run-finca-daily-checkin.sh"
REPORT_LABEL="ai.openclaw.finca.daily-report"
REPORT_PLIST="$HOME/Library/LaunchAgents/$REPORT_LABEL.plist"
REPORT_ENABLED_FILE="$HOME/.openclaw-finca/daily-report.enabled"
REPORT_RUNNER="$BIN_DIR/run-finca-daily-report.sh"
RETRY_INTERVAL_SECONDS="${FINCA_DAILY_RETRY_INTERVAL_SECONDS:-900}"

if [ "$ACTION" = "uninstall" ]; then
  launchctl bootout "gui/$(id -u)" "$CHECKIN_PLIST" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)" "$REPORT_PLIST" 2>/dev/null || true
  rm -f "$CHECKIN_PLIST" "$REPORT_PLIST"
  rm -f "$CHECKIN_ENABLED_FILE" "$REPORT_ENABLED_FILE"
  rm -f "$CHECKIN_RUNNER" "$REPORT_RUNNER"
  echo "Uninstalled Finca morning report and afternoon check-in"
  exit 0
fi

if [ "$ACTION" = "disable" ]; then
  rm -f "$CHECKIN_ENABLED_FILE" "$REPORT_ENABLED_FILE"
  echo "Finca schedules remain installed but disabled"
  exit 0
fi

if [ "$ACTION" != "install" ]; then
  echo "Usage: $0 [install|disable|uninstall]" >&2
  exit 2
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.openclaw-finca" "$BIN_DIR" /tmp/openclaw
install -m 700 "$ROOT/scripts/run-finca-daily-checkin.sh" "$CHECKIN_RUNNER"
install -m 700 "$ROOT/scripts/run-finca-daily-report.sh" "$REPORT_RUNNER"
touch "$CHECKIN_ENABLED_FILE" "$REPORT_ENABLED_FILE"

cat > "$CHECKIN_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$CHECKIN_LABEL</string>
  <key>ProgramArguments</key><array><string>$CHECKIN_RUNNER</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>16</integer><key>Minute</key><integer>0</integer></dict>
  <key>StartInterval</key><integer>$RETRY_INTERVAL_SECONDS</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/openclaw/finca-daily-checkin.stdout.log</string>
  <key>StandardErrorPath</key><string>/tmp/openclaw/finca-daily-checkin.stderr.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>TZ</key><string>America/Bogota</string>
  </dict>
</dict></plist>
PLIST

cat > "$REPORT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$REPORT_LABEL</string>
  <key>ProgramArguments</key><array><string>$REPORT_RUNNER</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>StartInterval</key><integer>$RETRY_INTERVAL_SECONDS</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/openclaw/finca-daily-report.stdout.log</string>
  <key>StandardErrorPath</key><string>/tmp/openclaw/finca-daily-report.stderr.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>TZ</key><string>America/Bogota</string>
  </dict>
</dict></plist>
PLIST

launchctl bootout "gui/$(id -u)" "$CHECKIN_PLIST" 2>/dev/null || true
launchctl bootout "gui/$(id -u)" "$REPORT_PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$CHECKIN_PLIST"
launchctl bootstrap "gui/$(id -u)" "$REPORT_PLIST"
echo "Installed Finca task report at 07:00 and progress check-in at 16:00 America/Bogota with ${RETRY_INTERVAL_SECONDS}s retry checks"
