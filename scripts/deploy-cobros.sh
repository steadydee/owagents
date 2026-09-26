#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/scripts/assert-release-ready.sh"
"$ROOT/scripts/smoke-cobros.sh"
WORKSPACE="${COBROS_WORKSPACE:-$HOME/.openclaw/workspace-owlswatch-cobros}"
CONFIG="${OPENCLAW_CONFIG_PATH:-$HOME/.openclaw-owlswatch/openclaw.json}"
BACKUP="$HOME/Backups/owlswatch-agents/deploy/cobros-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP" "$WORKSPACE/skills/cuenta-cobro" "$WORKSPACE/tools/owlswatch_cobros"
chmod 700 "$BACKUP"
cp -p "$CONFIG" "$BACKUP/openclaw.json"
rsync -a "$WORKSPACE/skills/cuenta-cobro/" "$BACKUP/skill/"
rsync -a "$WORKSPACE/tools/owlswatch_cobros/" "$BACKUP/tools/"
rsync -a "$ROOT/openclaw/agents/cobros/skills/cuenta-cobro/" "$WORKSPACE/skills/cuenta-cobro/"
rsync -a --delete --exclude '__pycache__' "$ROOT/tools/owlswatch_cobros/" "$WORKSPACE/tools/owlswatch_cobros/"
python3 - "$ROOT" "$CONFIG" <<'PY'
import json, os, sys
from pathlib import Path
root, config = map(Path, sys.argv[1:])
data = json.loads(config.read_text())
example = json.loads((root / 'openclaw/profiles/owlswatch/openclaw.example.json').read_text())
policy = next(a for a in example['agents']['list'] if a['id'] == 'cobros')['tools']['loopDetection']
agent = next(a for a in data['agents']['list'] if a['id'] == 'cobros')
agent['tools']['loopDetection'] = policy
temp = config.with_suffix('.cobros-deploy.tmp')
fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, 'w') as f:
    json.dump(data, f, indent=2); f.write('\n')
os.replace(temp, config)
PY
openclaw --profile owlswatch config validate
printf '%s\n' "Cobros deployed: $(git -C "$ROOT" rev-parse HEAD)" "Backup: $BACKUP"
printf '%s\n' 'Reload only the existing owlswatch gateway. Reset exhausted sessions through sessions.reset; do not edit session files.'
