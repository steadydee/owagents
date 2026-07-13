#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/scripts/assert-release-ready.sh"

WORKSPACE="${FINCA_WORKSPACE:-$HOME/.openclaw/workspace-finca-ops}"
PROFILE_DIR="${FINCA_PROFILE_DIR:-$HOME/.openclaw-finca}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/Backups/owlswatch-agents/finca-deploy}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$STAMP"

mkdir -p "$BACKUP_DIR" "$WORKSPACE/skills" "$WORKSPACE/tools/finca_tasks" "$PROFILE_DIR/secrets"

if [ -d "$WORKSPACE" ]; then
  rsync -a \
    --exclude memory \
    --exclude spool \
    --exclude mock \
    --exclude USER.md \
    --exclude MEMORY.md \
    "$WORKSPACE/" "$BACKUP_DIR/workspace/" 2>/dev/null || true
fi

rsync -a \
  "$ROOT/openclaw/agents/finca/AGENTS.md" \
  "$ROOT/openclaw/agents/finca/IDENTITY.md" \
  "$ROOT/openclaw/agents/finca/SOUL.md" \
  "$ROOT/openclaw/agents/finca/README.md" \
  "$ROOT/openclaw/agents/finca/TOOLS.md" \
  "$WORKSPACE/"
rsync -a --delete "$ROOT/openclaw/agents/finca/skills/" "$WORKSPACE/skills/"
rsync -a --delete --exclude '__pycache__' --exclude '.pytest_cache' "$ROOT/tools/finca_tasks/" "$WORKSPACE/tools/finca_tasks/"

if [ ! -f "$WORKSPACE/USER.md" ]; then
  cp "$ROOT/openclaw/agents/finca/USER.example.md" "$WORKSPACE/USER.md"
fi
if [ ! -f "$WORKSPACE/MEMORY.md" ]; then
  cp "$ROOT/openclaw/agents/finca/MEMORY.template.md" "$WORKSPACE/MEMORY.md"
fi
if [ ! -f "$PROFILE_DIR/openclaw.json" ]; then
  cp "$ROOT/openclaw/profiles/finca/openclaw.example.json" "$PROFILE_DIR/openclaw.json"
  chmod 600 "$PROFILE_DIR/openclaw.json"
  echo "Created $PROFILE_DIR/openclaw.json. Fill runtime placeholders before installing the gateway."
fi

OPENCLAW_GLOBAL_PACKAGE="${OPENCLAW_GLOBAL_PACKAGE:-$(npm root -g 2>/dev/null)/openclaw}"
if [ -d "$OPENCLAW_GLOBAL_PACKAGE" ]; then
  mkdir -p "$WORKSPACE/tools/finca_tasks/node_modules"
  ln -sfn "$OPENCLAW_GLOBAL_PACKAGE" "$WORKSPACE/tools/finca_tasks/node_modules/openclaw"
fi

python3 -m py_compile "$WORKSPACE/tools/finca_tasks/server.py"
python3 -m unittest discover -s "$ROOT/tools/finca_tasks/tests" >/dev/null

echo "Finca source deployed. Runtime state and secrets were preserved."
echo "Backup: $BACKUP_DIR"
echo "Deployed git commit: $(git -C "$ROOT" rev-parse HEAD)"
