#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-install}"
BIN_DIR="$HOME/.openclaw-finca/bin"
PROFILE_DIR="$HOME/.openclaw-finca"
LOG_DIR="${FINCA_SCHEDULE_LOG_DIR:-/tmp/openclaw}"
TOOL_RUNNER="$BIN_DIR/run-finca-tool-job.py"
GUI_DOMAIN="gui/$(id -u)"
USER_DOMAIN="user/$(id -u)"
SYSTEM_LAUNCH_DIR="${FINCA_SYSTEM_LAUNCH_DIR:-/Library/LaunchDaemons}"
CHECKIN_LABEL="ai.openclaw.finca.daily-checkin"
CHECKIN_PLIST="$HOME/Library/LaunchAgents/$CHECKIN_LABEL.plist"
CHECKIN_ENABLED_FILE="$HOME/.openclaw-finca/daily-checkin.enabled"
CHECKIN_RUNNER="$BIN_DIR/run-finca-daily-checkin.sh"
REPORT_LABEL="ai.openclaw.finca.daily-report"
REPORT_PLIST="$HOME/Library/LaunchAgents/$REPORT_LABEL.plist"
REPORT_ENABLED_FILE="$HOME/.openclaw-finca/daily-report.enabled"
REPORT_RUNNER="$BIN_DIR/run-finca-daily-report.sh"
RETRY_INTERVAL_SECONDS="${FINCA_DAILY_RETRY_INTERVAL_SECONDS:-900}"

if [ "$ACTION" = "disable" ]; then
  rm -f "$CHECKIN_ENABLED_FILE" "$REPORT_ENABLED_FILE"
  echo "Finca schedules remain installed but disabled"
  exit 0
fi

if [ "$ACTION" != "install" ] && [ "$ACTION" != "uninstall" ]; then
  echo "Usage: $0 [install|disable|uninstall]" >&2
  exit 2
fi

case "$RETRY_INTERVAL_SECONDS" in
  ''|*[!0-9]*|0) echo "FINCA_DAILY_RETRY_INTERVAL_SECONDS must be a positive integer" >&2; exit 2 ;;
esac

# Serialize installer invocations. A stale lock deliberately requires inspection;
# never guess that another installer is safe to interrupt.
umask 077
mkdir -p "$PROFILE_DIR"
INSTALL_LOCK="$PROFILE_DIR/schedule-install.lock"
if ! mkdir "$INSTALL_LOCK" 2>/dev/null; then
  echo "Finca schedule installation is locked; inspect the existing installer before retrying" >&2
  exit 1
fi
trap 'rmdir "$INSTALL_LOCK"' EXIT

# Do not confuse permission/launchd failures with an absent service. Keep all
# launchctl output private; a future job definition may contain environment data.
service_loaded() {
  if SERVICE_SNAPSHOT="$(LC_ALL=C launchctl print "$1" 2>&1)"; then
    return 0
  fi
  case "$SERVICE_SNAPSHOT" in
    *"Could not find service"*|*"Could not find specified service"*|*"Could not find domain"*) return 1 ;;
  esac
  echo "Cannot establish launchd ownership for $1; no activation will be attempted" >&2
  exit 1
}

assert_no_other_owner() {
  local label domain
  for label in "$CHECKIN_LABEL" "$REPORT_LABEL"; do
    if [ -f "$SYSTEM_LAUNCH_DIR/$label.plist" ]; then
      echo "Finca schedule has a system launch definition; refusing a second owner" >&2
      exit 1
    fi
    for domain in system "$USER_DOMAIN"; do
      if service_loaded "$domain/$label"; then
        echo "Finca schedule already belongs to $domain/$label; refusing a second owner" >&2
        exit 1
      fi
    done
  done
}

assert_gui_idle() {
  local label state
  for label in "$CHECKIN_LABEL" "$REPORT_LABEL"; do
    if service_loaded "$GUI_DOMAIN/$label"; then
      state="$(printf '%s\n' "$SERVICE_SNAPSHOT" | sed -n 's/^[[:space:]]*state = //p' | head -n 1)"
      if [ "$state" != "not running" ] || printf '%s\n' "$SERVICE_SNAPSHOT" | grep -Eq '^[[:space:]]*pid = [1-9][0-9]*'; then
        echo "Finca schedule $label is active or its state is unknown; wait for completion before replacing it" >&2
        exit 1
      fi
    fi
  done
}

stop_gui_jobs() {
  local label
  for label in "$CHECKIN_LABEL" "$REPORT_LABEL"; do
    if service_loaded "$GUI_DOMAIN/$label"; then
      if ! launchctl bootout "$GUI_DOMAIN/$label"; then
        echo "Could not stop $GUI_DOMAIN/$label; no replacement will be activated" >&2
        exit 1
      fi
      if service_loaded "$GUI_DOMAIN/$label"; then
        echo "$GUI_DOMAIN/$label remains loaded; refusing a duplicate schedule" >&2
        exit 1
      fi
    fi
  done
}

assert_no_other_owner
assert_gui_idle
if [ "$ACTION" = "install" ]; then
  for source in run-finca-daily-checkin.sh run-finca-daily-report.sh run-finca-tool-job.py; do
    if [ ! -f "$ROOT/scripts/$source" ]; then
      echo "Missing Finca schedule source: $source" >&2
      exit 1
    fi
  done
fi
stop_gui_jobs
assert_no_other_owner

if [ "$ACTION" = "uninstall" ]; then
  rm -f "$CHECKIN_PLIST" "$REPORT_PLIST"
  rm -f "$CHECKIN_ENABLED_FILE" "$REPORT_ENABLED_FILE"
  rm -f "$CHECKIN_RUNNER" "$REPORT_RUNNER" "$TOOL_RUNNER"
  echo "Uninstalled Finca morning report and afternoon check-in"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$BIN_DIR" "$LOG_DIR"
install -m 700 "$ROOT/scripts/run-finca-daily-checkin.sh" "$CHECKIN_RUNNER"
install -m 700 "$ROOT/scripts/run-finca-daily-report.sh" "$REPORT_RUNNER"
install -m 700 "$ROOT/scripts/run-finca-tool-job.py" "$TOOL_RUNNER"
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
  <key>StandardOutPath</key><string>$LOG_DIR/finca-daily-checkin.stdout.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/finca-daily-checkin.stderr.log</string>
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
  <key>StandardOutPath</key><string>$LOG_DIR/finca-daily-report.stdout.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/finca-daily-report.stderr.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>TZ</key><string>America/Bogota</string>
  </dict>
</dict></plist>
PLIST

assert_no_other_owner
for label in "$CHECKIN_LABEL" "$REPORT_LABEL"; do
  if service_loaded "$GUI_DOMAIN/$label"; then
    echo "$GUI_DOMAIN/$label appeared during installation; refusing activation" >&2
    exit 1
  fi
done
# launchd persists disabled overrides across bootout/bootstrap. Explicitly
# enable each service before bootstrap so a disabled schedule can be restored.
launchctl enable "$GUI_DOMAIN/$CHECKIN_LABEL"
launchctl enable "$GUI_DOMAIN/$REPORT_LABEL"
launchctl bootstrap "$GUI_DOMAIN" "$CHECKIN_PLIST"
launchctl bootstrap "$GUI_DOMAIN" "$REPORT_PLIST"
echo "Installed Finca task report at 07:00 and progress check-in at 16:00 America/Bogota with ${RETRY_INTERVAL_SECONDS}s retry checks"
