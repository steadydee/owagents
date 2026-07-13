#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/scripts/assert-release-ready.sh"
WORKSPACE="${HOTEL_WORKSPACE:-$HOME/.openclaw/workspace-hotel-ops}"
PROFILE_DIR="${HOTEL_PROFILE_DIR:-$HOME/.openclaw-hotel}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/Backups/hotel-agents/deploy}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$STAMP"

mkdir -p "$BACKUP_DIR"

backup_path() {
  local path="$1"
  local label="$2"
  if [ -e "$path" ]; then
    mkdir -p "$BACKUP_DIR/$label"
    rsync -a "$path" "$BACKUP_DIR/$label/"
  fi
}

echo "Backing up Hotel live source to $BACKUP_DIR"
backup_path "$WORKSPACE/AGENTS.md" hotel
backup_path "$WORKSPACE/IDENTITY.md" hotel
backup_path "$WORKSPACE/SOUL.md" hotel
backup_path "$WORKSPACE/README.md" hotel
backup_path "$WORKSPACE/TOOLS.md" hotel
backup_path "$WORKSPACE/skills" hotel-skills
backup_path "$WORKSPACE/tools/hotel_pms" hotel-tools

echo "Deploying Hotel source"
mkdir -p "$WORKSPACE/skills" "$WORKSPACE/tools/hotel_pms" "$PROFILE_DIR"
rsync -a "$ROOT/openclaw/agents/hotel/AGENTS.md" "$ROOT/openclaw/agents/hotel/IDENTITY.md" "$ROOT/openclaw/agents/hotel/SOUL.md" "$ROOT/openclaw/agents/hotel/README.md" "$ROOT/openclaw/agents/hotel/TOOLS.md" "$WORKSPACE/"
rsync -a --delete "$ROOT/openclaw/agents/hotel/skills/" "$WORKSPACE/skills/"
rsync -a --delete --exclude '__pycache__' --exclude '.pytest_cache' "$ROOT/tools/hotel_pms/" "$WORKSPACE/tools/hotel_pms/"

OPENCLAW_GLOBAL_PACKAGE="${OPENCLAW_GLOBAL_PACKAGE:-$(npm root -g 2>/dev/null)/openclaw}"
if [ -d "$OPENCLAW_GLOBAL_PACKAGE" ]; then
  mkdir -p "$WORKSPACE/tools/hotel_pms/node_modules"
  ln -sfn "$OPENCLAW_GLOBAL_PACKAGE" "$WORKSPACE/tools/hotel_pms/node_modules/openclaw"
else
  echo "Warning: could not find global OpenClaw package for Hotel plugin SDK link." >&2
fi

if [ ! -f "$WORKSPACE/USER.md" ]; then
  cp "$ROOT/openclaw/agents/hotel/USER.example.md" "$WORKSPACE/USER.md"
fi
if [ ! -f "$WORKSPACE/MEMORY.md" ]; then
  cp "$ROOT/openclaw/agents/hotel/MEMORY.template.md" "$WORKSPACE/MEMORY.md"
fi
if [ ! -f "$PROFILE_DIR/openclaw.json" ]; then
  cp "$ROOT/openclaw/profiles/hotel/openclaw.example.json" "$PROFILE_DIR/openclaw.json"
  echo "Created $PROFILE_DIR/openclaw.json from example. Fill secrets/chat ids before enabling Telegram."
fi

python3 -m py_compile "$WORKSPACE/tools/hotel_pms/server.py"
echo "Hotel deploy complete. Backup: $BACKUP_DIR"
echo "Deployed git commit: $(git -C "$ROOT" rev-parse HEAD)"
