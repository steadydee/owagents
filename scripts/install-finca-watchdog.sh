#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="ai.openclaw.finca.telegram-watchdog"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BIN_DIR="$HOME/.openclaw-finca/bin"
RUNNER="$BIN_DIR/watchdog-finca-telegram.sh"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-60}"

if [ "${1:-}" = "uninstall" ]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Uninstalled $LABEL"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$BIN_DIR" /tmp/openclaw
install -m 700 "$ROOT/scripts/watchdog-owlswatch-telegram.sh" "$RUNNER"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$RUNNER</string></array>
  <key>StartInterval</key><integer>$INTERVAL_SECONDS</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/openclaw/finca-telegram-watchdog.stdout.log</string>
  <key>StandardErrorPath</key><string>/tmp/openclaw/finca-telegram-watchdog.stderr.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/opt/node/bin:/opt/homebrew/bin:/usr/local/bin:/Users/agent/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>OPENCLAW_PROFILE</key><string>finca</string>
    <key>OPENCLAW_BIN</key><string>/Users/agent/.npm-global/bin/openclaw</string>
    <key>OPENCLAW_STATE_DIR</key><string>/Users/agent/.openclaw-finca</string>
    <key>STALE_SPOOL_SECONDS</key><string>90</string>
  </dict>
</dict></plist>
PLIST
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
echo "Installed $LABEL"
