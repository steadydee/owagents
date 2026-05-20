#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MAIN_WORKSPACE="${MAIN_WORKSPACE:-$HOME/.openclaw/workspace-owlswatch-main}"
CUENTA_WORKSPACE="${CUENTA_WORKSPACE:-$HOME/.openclaw/workspace-owlswatch}"
COTIZA_WORKSPACE="${COTIZA_WORKSPACE:-$HOME/.openclaw/workspace-owlswatch-cotiza}"
CORREO_WORKSPACE="${CORREO_WORKSPACE:-$HOME/.openclaw/workspace-owlswatch-correo}"
BRAIN_WORKSPACE="${BRAIN_WORKSPACE:-$HOME/.openclaw/workspace-dennis-brain}"
PROFILE="${OPENCLAW_PROFILE:-owlswatch}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/Backups/owlswatch-agents/deploy}"
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

echo "Backing up current live agent source to $BACKUP_DIR"
backup_path "$MAIN_WORKSPACE/AGENTS.md" main
backup_path "$MAIN_WORKSPACE/IDENTITY.md" main
backup_path "$MAIN_WORKSPACE/SOUL.md" main
backup_path "$MAIN_WORKSPACE/README.md" main
backup_path "$MAIN_WORKSPACE/TOOLS.md" main
backup_path "$MAIN_WORKSPACE/HEARTBEAT.md" main

backup_path "$CUENTA_WORKSPACE/AGENTS.md" cuenta
backup_path "$CUENTA_WORKSPACE/IDENTITY.md" cuenta
backup_path "$CUENTA_WORKSPACE/SOUL.md" cuenta
backup_path "$CUENTA_WORKSPACE/README.md" cuenta
backup_path "$CUENTA_WORKSPACE/TOOLS.md" cuenta
backup_path "$CUENTA_WORKSPACE/skills/intake-receipt" cuenta-skills
backup_path "$CUENTA_WORKSPACE/tools/owlswatch_intake" cuenta-tools

backup_path "$COTIZA_WORKSPACE/AGENTS.md" cotiza
backup_path "$COTIZA_WORKSPACE/IDENTITY.md" cotiza
backup_path "$COTIZA_WORKSPACE/SOUL.md" cotiza
backup_path "$COTIZA_WORKSPACE/README.md" cotiza
backup_path "$COTIZA_WORKSPACE/TOOLS.md" cotiza
backup_path "$COTIZA_WORKSPACE/skills/quote-draft" cotiza-skills
backup_path "$COTIZA_WORKSPACE/tools/owlswatch_quotes" cotiza-tools
backup_path "$COTIZA_WORKSPACE/assets" cotiza-assets

backup_path "$CORREO_WORKSPACE/AGENTS.md" correo
backup_path "$CORREO_WORKSPACE/IDENTITY.md" correo
backup_path "$CORREO_WORKSPACE/SOUL.md" correo
backup_path "$CORREO_WORKSPACE/README.md" correo
backup_path "$CORREO_WORKSPACE/TOOLS.md" correo
backup_path "$CORREO_WORKSPACE/skills/email-draft" correo-skills
backup_path "$CORREO_WORKSPACE/tools/owlswatch_email" correo-tools

backup_path "$BRAIN_WORKSPACE/AGENTS.md" brain
backup_path "$BRAIN_WORKSPACE/IDENTITY.md" brain
backup_path "$BRAIN_WORKSPACE/SOUL.md" brain
backup_path "$BRAIN_WORKSPACE/README.md" brain
backup_path "$BRAIN_WORKSPACE/TOOLS.md" brain
backup_path "$BRAIN_WORKSPACE/skills/brain-intake" brain-skills
backup_path "$BRAIN_WORKSPACE/tools/brain_intake" brain-tools

echo "Deploying main conductor source"
mkdir -p "$MAIN_WORKSPACE"
rsync -a "$ROOT/openclaw/agents/main/AGENTS.md" "$ROOT/openclaw/agents/main/IDENTITY.md" "$ROOT/openclaw/agents/main/SOUL.md" "$ROOT/openclaw/agents/main/README.md" "$ROOT/openclaw/agents/main/TOOLS.md" "$ROOT/openclaw/agents/main/HEARTBEAT.md" "$MAIN_WORKSPACE/"
if [ ! -f "$MAIN_WORKSPACE/USER.md" ]; then
  cp "$ROOT/openclaw/agents/main/USER.example.md" "$MAIN_WORKSPACE/USER.md"
fi
if [ ! -f "$MAIN_WORKSPACE/MEMORY.md" ]; then
  cp "$ROOT/openclaw/agents/main/MEMORY.template.md" "$MAIN_WORKSPACE/MEMORY.md"
fi

echo "Deploying Cuenta source"
mkdir -p "$CUENTA_WORKSPACE/skills/intake-receipt" "$CUENTA_WORKSPACE/tools/owlswatch_intake"
rsync -a "$ROOT/openclaw/agents/cuenta/AGENTS.md" "$ROOT/openclaw/agents/cuenta/IDENTITY.md" "$ROOT/openclaw/agents/cuenta/SOUL.md" "$ROOT/openclaw/agents/cuenta/README.md" "$ROOT/openclaw/agents/cuenta/TOOLS.md" "$CUENTA_WORKSPACE/"
rsync -a "$ROOT/openclaw/agents/cuenta/skills/intake-receipt/" "$CUENTA_WORKSPACE/skills/intake-receipt/"
rsync -a --delete --exclude '__pycache__' --exclude '.pytest_cache' "$ROOT/tools/owlswatch_intake/" "$CUENTA_WORKSPACE/tools/owlswatch_intake/"
if [ ! -f "$CUENTA_WORKSPACE/MEMORY.md" ]; then
  cp "$ROOT/openclaw/agents/cuenta/MEMORY.template.md" "$CUENTA_WORKSPACE/MEMORY.md"
fi

echo "Deploying Cotiza source"
mkdir -p "$COTIZA_WORKSPACE/skills/quote-draft" "$COTIZA_WORKSPACE/tools/owlswatch_quotes" "$COTIZA_WORKSPACE/assets" "$COTIZA_WORKSPACE/docs"
rsync -a "$ROOT/openclaw/agents/cotiza/AGENTS.md" "$ROOT/openclaw/agents/cotiza/IDENTITY.md" "$ROOT/openclaw/agents/cotiza/SOUL.md" "$ROOT/openclaw/agents/cotiza/README.md" "$ROOT/openclaw/agents/cotiza/TOOLS.md" "$COTIZA_WORKSPACE/"
rsync -a "$ROOT/openclaw/agents/cotiza/skills/quote-draft/" "$COTIZA_WORKSPACE/skills/quote-draft/"
rsync -a --delete --exclude '__pycache__' --exclude '.pytest_cache' "$ROOT/tools/owlswatch_quotes/" "$COTIZA_WORKSPACE/tools/owlswatch_quotes/"
rsync -a "$ROOT/openclaw/agents/cotiza/assets/" "$COTIZA_WORKSPACE/assets/"
rsync -a "$ROOT/openclaw/agents/cotiza/docs/" "$COTIZA_WORKSPACE/docs/"
if [ ! -f "$COTIZA_WORKSPACE/MEMORY.md" ]; then
  cp "$ROOT/openclaw/agents/cotiza/MEMORY.template.md" "$COTIZA_WORKSPACE/MEMORY.md"
fi

echo "Deploying Correo source"
mkdir -p "$CORREO_WORKSPACE/skills/email-draft" "$CORREO_WORKSPACE/tools/owlswatch_email" "$CORREO_WORKSPACE/docs" "$CORREO_WORKSPACE/tasks/email"
rsync -a "$ROOT/openclaw/agents/correo/AGENTS.md" "$ROOT/openclaw/agents/correo/IDENTITY.md" "$ROOT/openclaw/agents/correo/SOUL.md" "$ROOT/openclaw/agents/correo/README.md" "$ROOT/openclaw/agents/correo/TOOLS.md" "$CORREO_WORKSPACE/"
rsync -a "$ROOT/openclaw/agents/correo/skills/email-draft/" "$CORREO_WORKSPACE/skills/email-draft/"
rsync -a "$ROOT/openclaw/agents/correo/docs/" "$CORREO_WORKSPACE/docs/"
rsync -a --delete --exclude '__pycache__' --exclude '.pytest_cache' "$ROOT/tools/owlswatch_email/" "$CORREO_WORKSPACE/tools/owlswatch_email/"
if [ ! -f "$CORREO_WORKSPACE/MEMORY.md" ]; then
  cp "$ROOT/openclaw/agents/correo/MEMORY.template.md" "$CORREO_WORKSPACE/MEMORY.md"
fi

echo "Deploying Brain Intake source"
mkdir -p "$BRAIN_WORKSPACE/skills/brain-intake" "$BRAIN_WORKSPACE/tools/brain_intake"
rsync -a "$ROOT/openclaw/agents/brain/AGENTS.md" "$ROOT/openclaw/agents/brain/IDENTITY.md" "$ROOT/openclaw/agents/brain/SOUL.md" "$ROOT/openclaw/agents/brain/README.md" "$ROOT/openclaw/agents/brain/TOOLS.md" "$BRAIN_WORKSPACE/"
rsync -a "$ROOT/openclaw/agents/brain/skills/brain-intake/" "$BRAIN_WORKSPACE/skills/brain-intake/"
rsync -a --delete --exclude '__pycache__' --exclude '.pytest_cache' "$ROOT/tools/brain_intake/" "$BRAIN_WORKSPACE/tools/brain_intake/"
if [ ! -f "$BRAIN_WORKSPACE/USER.md" ]; then
  cp "$ROOT/openclaw/agents/brain/USER.example.md" "$BRAIN_WORKSPACE/USER.md"
fi
if [ ! -f "$BRAIN_WORKSPACE/MEMORY.md" ]; then
  cp "$ROOT/openclaw/agents/brain/MEMORY.template.md" "$BRAIN_WORKSPACE/MEMORY.md"
fi

echo "Validating deployed source"
python3 -m py_compile "$CUENTA_WORKSPACE/tools/owlswatch_intake/server.py"
python3 -m py_compile "$COTIZA_WORKSPACE/tools/owlswatch_quotes/server.py"
python3 -m py_compile "$CORREO_WORKSPACE/tools/owlswatch_email/server.py"
python3 -m py_compile "$BRAIN_WORKSPACE/tools/brain_intake/server.py"
openclaw --profile "$PROFILE" config validate
openclaw --profile "$PROFILE" skills check --agent main
openclaw --profile "$PROFILE" skills check --agent cuenta
openclaw --profile "$PROFILE" skills check --agent cotiza
openclaw --profile "$PROFILE" skills check --agent correo
openclaw --profile "$PROFILE" skills check --agent brain

echo "Deploy complete. Backup: $BACKUP_DIR"
echo "Restart with: openclaw --profile $PROFILE gateway restart"
