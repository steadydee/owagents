#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL_DIR="$ROOT/tools/owlswatch_payroll"
PYTHON="${NOMINA_SMOKE_PYTHON:-${OWLSWATCH_PAYROLL_PYTHON:-python3}}"
SMOKE_DIR="$(mktemp -d)"
trap 'rm -rf "$SMOKE_DIR"' EXIT
chmod 700 "$SMOKE_DIR"
export PYTHONPYCACHEPREFIX="$SMOKE_DIR/pycache"
export OWLSWATCH_PAYROLL_WORKSPACE="$SMOKE_DIR/workspace"
export OWLSWATCH_PAYROLL_PYTHON="$PYTHON"
unset OWLSWATCH_PAYROLL_CONFIG OWLSWATCH_PAYROLL_TRUSTED_CONTEXT
mkdir -m 700 "$OWLSWATCH_PAYROLL_WORKSPACE"

cd "$ROOT"
"$PYTHON" -m compileall -q "$TOOL_DIR"
"$PYTHON" -m unittest discover -s "$TOOL_DIR/tests" -p 'test_*.py' -v
node_tests=()
while IFS= read -r test_path; do
  node_tests+=("$test_path")
done < <(rg --files "$TOOL_DIR" -g '*.test.mjs')
if [ "${#node_tests[@]}" -eq 0 ]; then
  echo "Nómina native-plugin tests are missing." >&2
  exit 1
fi
node --test "${node_tests[@]}"
"$PYTHON" "$TOOL_DIR/server.py" catalog > "$SMOKE_DIR/catalog.json"

"$PYTHON" - "$ROOT" "$SMOKE_DIR/catalog.json" <<'PY'
import json
import re
import sys
from pathlib import Path

root, catalog_path = map(Path, sys.argv[1:])
catalog = json.loads(catalog_path.read_text())
profile = json.loads((root / 'openclaw/profiles/nomina/openclaw.example.json').read_text())
agent = profile['agents']['list'][0]
allowed = set(agent['tools']['alsoAllow'])
assert set(catalog) == allowed, 'Payroll catalog and specialist allowlist differ'
assert all(re.fullmatch(r'nomina_[a-z_]+', name) for name in catalog)
for name, entry in catalog.items():
    assert entry['description'] and entry['parameters']['type'] == 'object', name
skill = (root / 'openclaw/agents/nomina/skills/payroll/SKILL.md').read_text()
skill_tools = set(re.findall(r'\bnomina_[a-z_]+\b', skill))
assert skill_tools <= allowed, f'Skill grants missing tools: {skill_tools - allowed}'
assert allowed <= skill_tools, f'Skill omits published workflow tools: {allowed - skill_tools}'
boundaries = (root / 'docs/security-boundaries.md').read_text()
tool_doc = (root / 'openclaw/agents/nomina/TOOLS.md').read_text()
for name in allowed:
    assert f'`{name}`' in boundaries, f'Security boundary missing {name}'
    assert f'`{name}`' in tool_doc, f'Tool documentation missing {name}'
denies = {'exec', 'browser', 'gateway', 'cron', 'nodes', 'canvas', 'group:fs', 'group:web', 'bundle-mcp', 'message'}
assert agent['tools']['profile'] == 'minimal'
assert denies <= set(agent['tools']['deny'])
assert agent['id'] == 'nomina' and agent['skills'] == ['payroll']
assert profile['channels']['telegram']['enabled'] is False
assert profile['channels']['telegram']['dmPolicy'] == 'allowlist'
assert profile['channels']['telegram']['groupPolicy'] == 'disabled'
assert profile['agents']['defaults']['heartbeat']['every'] == '0m'
assert not profile.get('mcp', {}).get('servers'), 'Nómina must have one native transport'
assert profile['plugins']['entries']['owlswatch-payroll']['hooks']['allowConversationAccess'] is True
assert profile['plugins']['entries']['owlswatch-runtime-guard']['hooks']['allowConversationAccess'] is True
assert '/confirmar_nomina TOKEN' in skill
assert '/revisar_nomina TOKEN' in skill
config = json.loads((root / 'openclaw/profiles/nomina/payroll-config.example.json').read_text())
assert config['enabled'] is False and config['archive']['enabled'] is False
assert config['telegram']['allowed_sender_ids'] == ['0']
assert config['telegram']['allowed_routes'] == [{'chat_id': '0', 'thread_id': ''}]
for filename in ('AGENTS.md', 'SOUL.md', 'IDENTITY.md', 'TOOLS.md', 'USER.example.md', 'MEMORY.template.md', 'README.md'):
    assert (root / 'openclaw/agents/nomina' / filename).is_file(), filename
print('Nómina catalog, workflow, profile and authority contract passed.')
PY

bash -n "$ROOT/scripts/deploy-nomina-to-mac-mini.sh"
echo "Nómina smoke passed (synthetic data; no live payroll changes)."
