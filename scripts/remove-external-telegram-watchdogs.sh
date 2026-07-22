#!/usr/bin/env bash
set -euo pipefail

# OpenClaw owns Telegram polling recovery and channel-health restarts. These
# legacy LaunchAgents can interrupt durable update replay during startup, so
# remove them whenever agent source is deployed.
labels=(
  "ai.openclaw.owlswatch.telegram-watchdog"
  "ai.openclaw.hotel.telegram-watchdog"
  "ai.openclaw.finca.telegram-watchdog"
)

for label in "${labels[@]}"; do
  plist="$HOME/Library/LaunchAgents/$label.plist"
  launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
  rm -f "$plist"
done

rm -f \
  "$HOME/.openclaw-hotel/bin/watchdog-hotel-telegram.sh" \
  "$HOME/.openclaw-finca/bin/watchdog-finca-telegram.sh"

echo "Removed legacy external Telegram watchdogs."
