#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/scripts/assert-release-ready.sh"
"$ROOT/scripts/smoke-cuenta.sh"
WORKSPACE="${CUENTA_WORKSPACE:-$HOME/.openclaw/workspace-owlswatch}"
BACKUP="${BACKUP_ROOT:-$HOME/Backups/owlswatch-agents/deploy}/cuenta-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP" "$WORKSPACE/skills/intake-receipt" "$WORKSPACE/tools/owlswatch_intake"

for file in AGENTS.md TOOLS.md IDENTITY.md SOUL.md README.md; do
  if [ -f "$WORKSPACE/$file" ]; then cp -p "$WORKSPACE/$file" "$BACKUP/"; fi
done
rsync -a "$WORKSPACE/skills/intake-receipt/" "$BACKUP/skill/"
rsync -a "$WORKSPACE/tools/owlswatch_intake/" "$BACKUP/tools/"
for file in AGENTS.md TOOLS.md IDENTITY.md SOUL.md README.md; do
  cp "$ROOT/openclaw/agents/cuenta/$file" "$WORKSPACE/$file"
done
rsync -a "$ROOT/openclaw/agents/cuenta/skills/intake-receipt/" "$WORKSPACE/skills/intake-receipt/"
rsync -a --delete --exclude '__pycache__' --exclude '.pytest_cache' "$ROOT/tools/owlswatch_intake/" "$WORKSPACE/tools/owlswatch_intake/"
# rsync replaces the plugin directory, including its runtime-only SDK link.
# Restore the installed SDK before the existing gateway reloads the plugin.
OPENCLAW_GLOBAL_PACKAGE="${OPENCLAW_GLOBAL_PACKAGE:-$(npm root -g 2>/dev/null)/openclaw}"
test -d "$OPENCLAW_GLOBAL_PACKAGE" || { echo "OpenClaw SDK is missing" >&2; exit 1; }
mkdir -p "$WORKSPACE/tools/owlswatch_intake/node_modules"
ln -sfn "$OPENCLAW_GLOBAL_PACKAGE" "$WORKSPACE/tools/owlswatch_intake/node_modules/openclaw"
python3 -m py_compile "$WORKSPACE/tools/owlswatch_intake/server.py"
node --input-type=module - "$WORKSPACE" <<'JS'
import { pathToFileURL } from "node:url";
const workspace = process.argv[2];
const { createReceiptPromptHook } = await import(pathToFileURL(`${workspace}/tools/owlswatch_intake/prompt-context.mjs`));
if (!createReceiptPromptHook(workspace)({}, { agentId: "cuenta" })?.appendSystemContext) {
  throw new Error("Deployed Cuenta skill was not loaded");
}
JS
# Preserve diagnostics for the existing service; do not install another poller.
PLIST="$HOME/Library/LaunchAgents/ai.openclaw.owlswatch.plist"
if [ -f "$PLIST" ]; then
  cp -p "$PLIST" "$BACKUP/gateway.plist"
  LOG_DIR="$HOME/.openclaw-owlswatch/logs"
  mkdir -p "$LOG_DIR"
  chmod 700 "$LOG_DIR"
  /usr/libexec/PlistBuddy -c "Set :StandardErrorPath $LOG_DIR/gateway.error.log" "$PLIST"
fi
openclaw --profile owlswatch config validate
printf '%s\n' "Cuenta deployed: $(git -C "$ROOT" rev-parse HEAD)" "Backup: $BACKUP"
printf '%s\n' 'Reload only the existing owlswatch LaunchAgent to apply the error-log path, then verify plugin loading, channel health and a fresh Cuenta turn.'
