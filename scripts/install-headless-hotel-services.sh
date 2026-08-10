#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-install}"
TARGET_USER="${OPENCLAW_SERVICE_USER:-${SUDO_USER:-agent}}"
TARGET_UID="$(id -u "$TARGET_USER")"
TARGET_GROUP="$(id -gn "$TARGET_USER")"
TARGET_HOME="${OPENCLAW_SERVICE_HOME:-$(dscl . -read "/Users/$TARGET_USER" NFSHomeDirectory | awk '{print $2}')}"
SOURCE_DIR="${OPENCLAW_LAUNCHAGENT_DIR:-$TARGET_HOME/Library/LaunchAgents}"
DAEMON_DIR="${OPENCLAW_LAUNCHDAEMON_DIR:-/Library/LaunchDaemons}"

LABELS=(
  ai.openclaw.hotel
  ai.openclaw.hotel.telegram-observer
  ai.openclaw.hotel.daily-summary
  ai.openclaw.hotel.registro-pickup
)

usage() {
  cat <<'EOF'
Usage: install-headless-hotel-services.sh [render|install|uninstall|status]

render     Render and validate LaunchDaemons without loading them.
install    Install boot-level Hotel services. Run with sudo.
uninstall  Remove boot-level services and restore user LaunchAgents. Run with sudo.
status     Show system and user service state.
EOF
}

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "This action requires sudo." >&2
    exit 77
  fi
}

plist_set() {
  local plist="$1"
  local key="$2"
  local type="$3"
  local value="$4"
  /usr/libexec/PlistBuddy -c "Delete :$key" "$plist" >/dev/null 2>&1 || true
  /usr/libexec/PlistBuddy -c "Add :$key $type $value" "$plist"
}

render_one() {
  local label="$1"
  local source="$SOURCE_DIR/$label.plist"
  local destination="$DAEMON_DIR/$label.plist"

  if [ ! -f "$source" ]; then
    echo "Missing source LaunchAgent: $source" >&2
    return 1
  fi

  mkdir -p "$DAEMON_DIR"
  cp "$source" "$destination"
  plist_set "$destination" UserName string "$TARGET_USER"
  plist_set "$destination" GroupName string "$TARGET_GROUP"

  /usr/libexec/PlistBuddy -c "Add :EnvironmentVariables dict" "$destination" >/dev/null 2>&1 || true
  plist_set "$destination" EnvironmentVariables:HOME string "$TARGET_HOME"
  plist_set "$destination" EnvironmentVariables:USER string "$TARGET_USER"
  plist_set "$destination" EnvironmentVariables:LOGNAME string "$TARGET_USER"
  plist_set "$destination" EnvironmentVariables:PATH string "$TARGET_HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

  plutil -lint "$destination" >/dev/null
  if [ "$(id -u)" -eq 0 ]; then
    chown root:wheel "$destination"
    chmod 644 "$destination"
  fi
  echo "Rendered $destination"
}

render_all() {
  local label
  for label in "${LABELS[@]}"; do
    render_one "$label"
  done
}

disable_user_service() {
  local label="$1"
  local source="$SOURCE_DIR/$label.plist"
  launchctl bootout "gui/$TARGET_UID" "$source" >/dev/null 2>&1 || true
  launchctl disable "gui/$TARGET_UID/$label" >/dev/null 2>&1 || true
}

enable_user_service() {
  local label="$1"
  local source="$SOURCE_DIR/$label.plist"
  launchctl enable "gui/$TARGET_UID/$label" >/dev/null 2>&1 || true
  if launchctl print "gui/$TARGET_UID" >/dev/null 2>&1 && [ -f "$source" ]; then
    launchctl bootstrap "gui/$TARGET_UID" "$source" >/dev/null 2>&1 || true
  fi
}

install_all() {
  require_root
  render_all

  local label destination
  for label in "${LABELS[@]}"; do
    disable_user_service "$label"
    destination="$DAEMON_DIR/$label.plist"
    launchctl bootout "system/$label" >/dev/null 2>&1 || true
    launchctl bootstrap system "$destination"
    launchctl enable "system/$label"
  done

  echo "Installed boot-level Hotel services for $TARGET_USER."
}

uninstall_all() {
  require_root

  local label destination
  for label in "${LABELS[@]}"; do
    destination="$DAEMON_DIR/$label.plist"
    launchctl bootout "system/$label" >/dev/null 2>&1 || true
    launchctl disable "system/$label" >/dev/null 2>&1 || true
    rm -f "$destination"
    enable_user_service "$label"
  done

  echo "Removed boot-level Hotel services and restored user LaunchAgents."
}

show_status() {
  local label
  for label in "${LABELS[@]}"; do
    echo "--- $label"
    if launchctl print "system/$label" >/dev/null 2>&1; then
      echo "system: loaded"
    else
      echo "system: not loaded"
    fi
    if launchctl print "gui/$TARGET_UID/$label" >/dev/null 2>&1; then
      echo "user: loaded"
    else
      echo "user: not loaded"
    fi
  done
}

case "$ACTION" in
  render)
    render_all
    ;;
  install)
    install_all
    ;;
  uninstall)
    uninstall_all
    ;;
  status)
    show_status
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
