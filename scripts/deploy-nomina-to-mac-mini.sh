#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE_ONLY=0
if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != "--stage-only" ]; }; then
  echo "Usage: $0 [--stage-only]" >&2
  exit 2
fi
if [ "$#" -eq 1 ]; then STAGE_ONLY=1; fi
# This gate freshly fetches main, requires a clean matching checkout and scans secrets.
"$ROOT/scripts/assert-release-ready.sh"
WORKSPACE="${OWLSWATCH_PAYROLL_WORKSPACE:-$HOME/.openclaw/workspace-nomina-nomina}"
PROFILE_DIR="$HOME/.openclaw-nomina"
BACKUP_ROOT="${NOMINA_DEPLOY_BACKUP_ROOT:-$HOME/Backups/nomina/source-deploys}"
CONFIG="${OWLSWATCH_PAYROLL_CONFIG:-$WORKSPACE/payroll-config.json}"
PROFILE_CONFIG="$PROFILE_DIR/openclaw.json"
BASE_PYTHON="${NOMINA_BASE_PYTHON:-python3}"

# Never bootstrap a live bot, widen allowlists or silently copy disabled examples.
python3 - "$WORKSPACE" "$CONFIG" "$PROFILE_CONFIG" "$STAGE_ONLY" "$ROOT" <<'PY'
import json
import os
import re
import stat
import sys
from pathlib import Path

workspace, config_path, profile_path = [Path(value).expanduser() for value in sys.argv[1:4]]
stage_only = sys.argv[4] == '1'
root = Path(sys.argv[5])
def require(condition, message):
    if not condition:
        raise SystemExit(f'Nómina deploy blocked: {message}')
require(workspace.is_absolute() and workspace not in (Path('/'), Path.home(), root), 'choose a dedicated absolute payroll workspace')
for relative in ('', 'docs', 'skills', 'skills/payroll', 'tools', 'tools/owlswatch_payroll', 'tools/agent_runtime_guard', '.venv'):
    require(not (workspace / relative).is_symlink(), 'deployment directories must not be symbolic links')
if workspace.exists():
    require(workspace.is_dir() and workspace.stat().st_uid == os.getuid(), 'workspace must belong to the runtime owner')
    require(stat.S_IMODE(workspace.stat().st_mode) == 0o700, 'workspace permissions must be 700')
if stage_only:
    print('Nómina staging only: live profile and payroll enablement are not required.')
    raise SystemExit(0)
require(workspace.is_dir(), 'configure a private workspace first, or use --stage-only')
require(config_path.is_file() and not config_path.is_symlink(), 'restricted payroll-config.json is missing')
require(config_path.resolve().is_relative_to(workspace.resolve()), 'payroll config must stay inside its workspace')
require(stat.S_IMODE(config_path.stat().st_mode) == 0o600, 'payroll config permissions must be 600')
require(profile_path.is_file() and not profile_path.is_symlink(), 'configure the dedicated nomina OpenClaw profile first')
require(stat.S_IMODE(profile_path.stat().st_mode) == 0o600, 'OpenClaw profile permissions must be 600')
config = json.loads(config_path.read_text())
profile = json.loads(profile_path.read_text())
require(config.get('schema_version') == 1 and config.get('enabled') is True, 'review and enable payroll configuration before deployment')
tg = config.get('telegram', {})
senders = tg.get('allowed_sender_ids', [])
routes = tg.get('allowed_routes', [])
require(bool(senders) and all(re.fullmatch(r'[1-9]\d{0,19}', str(sender)) for sender in senders), 'set explicit numeric manager IDs')
require(bool(routes), 'configure at least one private route')
for route in routes:
    require(bool(re.fullmatch(r'-?[1-9]\d{0,19}', str(route.get('chat_id', '')))), 'set numeric private route IDs')
    thread = str(route.get('thread_id', ''))
    require(thread == '' or bool(re.fullmatch(r'[1-9]\d{0,19}', thread)), 'topic IDs must be numeric or empty')
channel = profile.get('channels', {}).get('telegram', {})
require(channel.get('enabled') is True, 'Telegram must be explicitly configured and enabled')
require(channel.get('dmPolicy') == 'allowlist' and channel.get('groupPolicy') in ('disabled', 'allowlist'), 'Telegram must remain allowlisted')
require(set(map(str, channel.get('allowFrom', []))) == set(map(str, senders)), 'Telegram and payroll manager allowlists must match')
for value in (channel.get('botToken', ''), profile.get('gateway', {}).get('auth', {}).get('token', '')):
    require(isinstance(value, str) and len(value) >= 20 and '<' not in value, 'set bot and gateway tokens locally')
agents = profile.get('agents', {}).get('list', [])
require(len(agents) == 1 and agents[0].get('id') == 'nomina', 'use a dedicated profile containing only nomina')
require(Path(agents[0].get('workspace', '')).expanduser().resolve() == workspace.resolve(), 'agent workspace differs from deployment target')
example = json.loads((root / 'openclaw/profiles/nomina/openclaw.example.json').read_text())
expected_tools = example['agents']['list'][0]['tools']
actual_tools = agents[0].get('tools', {})
require(actual_tools.get('profile') == 'minimal' and set(actual_tools.get('alsoAllow', [])) == set(expected_tools['alsoAllow']) and set(expected_tools['deny']) <= set(actual_tools.get('deny', [])), 'live specialist authority differs from the reviewed catalog/policy')
require(not profile.get('mcp', {}).get('servers'), 'use only the native plugin transport')
for plugin in ('owlswatch-payroll', 'owlswatch-runtime-guard'):
    entry = profile.get('plugins', {}).get('entries', {}).get(plugin, {})
    require(entry.get('enabled') is True and entry.get('hooks', {}).get('allowConversationAccess') is True, 'required native plugins/hooks are missing')
env = profile.get('env', {}).get('vars', {})
require(env.get('OWLSWATCH_PAYROLL_WORKSPACE') == str(workspace.resolve()), 'profile must pin the absolute payroll workspace')
require(env.get('OWLSWATCH_PAYROLL_PYTHON') == str(workspace.resolve() / '.venv/bin/python3'), 'profile must pin its isolated Python interpreter')
print('Nómina private deployment configuration checked.')
PY

# Bootstrap/verify the isolated environment before replacing any source files.
mkdir -p "$WORKSPACE"
if [ ! -x "$WORKSPACE/.venv/bin/python3" ]; then "$BASE_PYTHON" -m venv "$WORKSPACE/.venv"; fi
"$WORKSPACE/.venv/bin/python3" -m pip install --disable-pip-version-check -r "$ROOT/tools/owlswatch_payroll/requirements.txt"
"$WORKSPACE/.venv/bin/python3" -c 'import google.auth, googleapiclient.discovery, cryptography.fernet'
NOMINA_SMOKE_PYTHON="$WORKSPACE/.venv/bin/python3" "$ROOT/scripts/smoke-nomina.sh"
OPENCLAW_GLOBAL_PACKAGE="${OPENCLAW_GLOBAL_PACKAGE:-$(npm root -g)/openclaw}"
if [ ! -d "$OPENCLAW_GLOBAL_PACKAGE" ]; then
  echo "Nómina deploy blocked: the installed OpenClaw SDK was not found." >&2
  exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$STAMP"
mkdir -p "$BACKUP_DIR/source" "$WORKSPACE/docs" "$WORKSPACE/skills" "$WORKSPACE/tools/owlswatch_payroll" "$WORKSPACE/tools/agent_runtime_guard"
chmod 700 "$BACKUP_DIR"
# Only source files are copied here. The service owns consistent encrypted data backups.
for filename in AGENTS.md IDENTITY.md SOUL.md README.md TOOLS.md; do
  if [ -f "$WORKSPACE/$filename" ]; then cp "$WORKSPACE/$filename" "$BACKUP_DIR/source/"; fi
done
if [ -d "$WORKSPACE/skills/payroll" ]; then rsync -a "$WORKSPACE/skills/payroll" "$BACKUP_DIR/source/"; fi
if [ -d "$WORKSPACE/tools/owlswatch_payroll" ]; then
  rsync -a --exclude node_modules --exclude .venv --exclude __pycache__ --exclude state --exclude secrets --exclude snapshots --exclude exports --exclude archives --exclude backups --exclude payroll-config.json "$WORKSPACE/tools/owlswatch_payroll/" "$BACKUP_DIR/source/tools/"
fi
if [ -d "$WORKSPACE/tools/agent_runtime_guard" ]; then
  rsync -a --exclude node_modules --exclude __pycache__ "$WORKSPACE/tools/agent_runtime_guard/" "$BACKUP_DIR/source/runtime-guard/"
fi

for filename in AGENTS.md IDENTITY.md SOUL.md README.md TOOLS.md; do
  cp "$ROOT/openclaw/agents/nomina/$filename" "$WORKSPACE/$filename"
done
rsync -a --delete "$ROOT/openclaw/agents/nomina/skills/payroll/" "$WORKSPACE/skills/payroll/"
rsync -a --delete --exclude node_modules --exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude state --exclude secrets --exclude snapshots --exclude exports --exclude archives --exclude backups --exclude payroll-config.json "$ROOT/tools/owlswatch_payroll/" "$WORKSPACE/tools/owlswatch_payroll/"
rsync -a --delete --exclude node_modules --exclude __pycache__ "$ROOT/tools/agent_runtime_guard/" "$WORKSPACE/tools/agent_runtime_guard/"
rsync -a "$ROOT"/docs/nomina*.md "$WORKSPACE/docs/"
if [ ! -f "$WORKSPACE/USER.md" ]; then cp "$ROOT/openclaw/agents/nomina/USER.example.md" "$WORKSPACE/USER.md"; fi
if [ ! -f "$WORKSPACE/MEMORY.md" ]; then cp "$ROOT/openclaw/agents/nomina/MEMORY.template.md" "$WORKSPACE/MEMORY.md"; fi
mkdir -p "$WORKSPACE/tools/owlswatch_payroll/node_modules" "$WORKSPACE/tools/agent_runtime_guard/node_modules"
ln -sfn "$OPENCLAW_GLOBAL_PACKAGE" "$WORKSPACE/tools/owlswatch_payroll/node_modules/openclaw"
ln -sfn "$OPENCLAW_GLOBAL_PACKAGE" "$WORKSPACE/tools/agent_runtime_guard/node_modules/openclaw"
OWLSWATCH_PAYROLL_WORKSPACE="$WORKSPACE" "$WORKSPACE/.venv/bin/python3" "$WORKSPACE/tools/owlswatch_payroll/server.py" catalog >/dev/null
if [ "$STAGE_ONLY" -eq 1 ]; then
  python3 "$ROOT/scripts/record-release.py" --profile nomina --workspace "$WORKSPACE" --staged
  echo "Nómina source staged; no gateway was activated. Source backup: $BACKUP_DIR"
  echo "Staged git commit: $(git -C "$ROOT" rev-parse HEAD)"
  exit 0
fi
export OPENCLAW_CONFIG_PATH="$PROFILE_CONFIG"
openclaw --profile nomina config validate
openclaw --profile nomina skills check
openclaw --profile nomina gateway restart
openclaw --profile nomina gateway status
openclaw --profile nomina channels status --probe
python3 "$ROOT/scripts/record-release.py" --profile nomina --workspace "$WORKSPACE"
echo "Nómina source deploy complete. Source backup: $BACKUP_DIR"
echo "Deployed git commit: $(git -C "$ROOT" rev-parse HEAD)"
