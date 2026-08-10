#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

OPENCLAW_SERVICE_USER="$(id -un)" \
OPENCLAW_SERVICE_HOME="$HOME" \
OPENCLAW_LAUNCHAGENT_DIR="$HOME/Library/LaunchAgents" \
OPENCLAW_LAUNCHDAEMON_DIR="$TMP" \
  "$ROOT/scripts/install-headless-hotel-services.sh" render >/dev/null

labels=(
  ai.openclaw.hotel
  ai.openclaw.hotel.telegram-observer
  ai.openclaw.hotel.daily-summary
  ai.openclaw.hotel.registro-pickup
)

for label in "${labels[@]}"; do
  plist="$TMP/$label.plist"
  test -f "$plist"
  plutil -lint "$plist" >/dev/null
  test "$(/usr/libexec/PlistBuddy -c 'Print :UserName' "$plist")" = "$(id -un)"
  test "$(/usr/libexec/PlistBuddy -c 'Print :EnvironmentVariables:HOME' "$plist")" = "$HOME"
  test "$(/usr/libexec/PlistBuddy -c 'Print :EnvironmentVariables:PATH' "$plist")" != ""
done

test "$(/usr/libexec/PlistBuddy -c 'Print :KeepAlive' "$TMP/ai.openclaw.hotel.plist")" = "true"
test "$(/usr/libexec/PlistBuddy -c 'Print :StartInterval' "$TMP/ai.openclaw.hotel.telegram-observer.plist")" = "120"
test "$(/usr/libexec/PlistBuddy -c 'Print :StartCalendarInterval:Hour' "$TMP/ai.openclaw.hotel.daily-summary.plist")" = "16"
test "$(/usr/libexec/PlistBuddy -c 'Print :StartCalendarInterval:Hour' "$TMP/ai.openclaw.hotel.registro-pickup.plist")" = "17"

echo "Headless Hotel service rendering passed"
