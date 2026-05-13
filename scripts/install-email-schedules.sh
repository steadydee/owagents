#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-install}"
PLIST_DIR="$HOME/Library/LaunchAgents"
ENABLED_FILE="$HOME/.openclaw-owlswatch/email-agent.enabled"

declare -A LABELS=(
  [poll]="ai.openclaw.owlswatch.email-poll"
  [daily]="ai.openclaw.owlswatch.email-daily-summary"
  [unanswered]="ai.openclaw.owlswatch.email-unanswered"
)

unload_one() {
  local label="$1"
  local plist="$PLIST_DIR/$label.plist"
  launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
  rm -f "$plist"
}

if [ "$ACTION" = "uninstall" ]; then
  for label in "${LABELS[@]}"; do
    unload_one "$label"
  done
  rm -f "$ENABLED_FILE"
  echo "Uninstalled Correo email schedules"
  exit 0
fi

if [ "$ACTION" = "disable" ]; then
  rm -f "$ENABLED_FILE"
  echo "Correo schedules left installed but disabled"
  exit 0
fi

if [ "$ACTION" != "install" ]; then
  echo "Usage: $0 [install|disable|uninstall]" >&2
  exit 2
fi

mkdir -p "$PLIST_DIR" "$(dirname "$ENABLED_FILE")" /tmp/openclaw
chmod +x "$ROOT/scripts/run-correo-poll.sh" "$ROOT/scripts/run-correo-daily-summary.sh" "$ROOT/scripts/run-correo-unanswered.sh"
touch "$ENABLED_FILE"

cat > "$PLIST_DIR/${LABELS[poll]}.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABELS[poll]}</string>
  <key>ProgramArguments</key><array><string>$ROOT/scripts/run-correo-poll.sh</string></array>
  <key>StartInterval</key><integer>1800</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/openclaw/owlswatch-email-poll.stdout.log</string>
  <key>StandardErrorPath</key><string>/tmp/openclaw/owlswatch-email-poll.stderr.log</string>
</dict>
</plist>
PLIST

cat > "$PLIST_DIR/${LABELS[daily]}.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABELS[daily]}</string>
  <key>ProgramArguments</key><array><string>$ROOT/scripts/run-correo-daily-summary.sh</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>/tmp/openclaw/owlswatch-email-daily-summary.stdout.log</string>
  <key>StandardErrorPath</key><string>/tmp/openclaw/owlswatch-email-daily-summary.stderr.log</string>
</dict>
</plist>
PLIST

cat > "$PLIST_DIR/${LABELS[unanswered]}.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABELS[unanswered]}</string>
  <key>ProgramArguments</key><array><string>$ROOT/scripts/run-correo-unanswered.sh</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>15</integer></dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>/tmp/openclaw/owlswatch-email-unanswered.stdout.log</string>
  <key>StandardErrorPath</key><string>/tmp/openclaw/owlswatch-email-unanswered.stderr.log</string>
</dict>
</plist>
PLIST

for label in "${LABELS[@]}"; do
  plist="$PLIST_DIR/$label.plist"
  launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
done

echo "Installed Correo email schedules"
echo "Poll: every 30 minutes"
echo "Daily summary: 08:00"
echo "Unanswered scan: 08:15"
echo "Disable without uninstall: $0 disable"
