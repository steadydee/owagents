#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/tools/owlswatch_intake/server.py"
PROFILE="$ROOT/openclaw/profiles/owlswatch/openclaw.example.json"
SKILL="$ROOT/openclaw/agents/cuenta/skills/intake-receipt/SKILL.md"

python3 -m py_compile "$SERVER"
python3 -m unittest discover -s "$ROOT/tools/owlswatch_intake/tests" -p 'test_*.py'
node --test "$ROOT/tools/owlswatch_intake/tests/"*.test.mjs
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n' | python3 "$SERVER" >/tmp/owlswatch-cuenta-tools.json
grep -q 'owlswatch_operations_create_expense_draft' /tmp/owlswatch-cuenta-tools.json
grep -q 'owlswatch_album_buffer_check' /tmp/owlswatch-cuenta-tools.json
rm -f /tmp/owlswatch-cuenta-tools.json

python3 - "$PROFILE" "$SKILL" "$ROOT" <<'PY'
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[3]) / 'scripts'))
from config_agents import configured_agents

profile = json.loads(Path(sys.argv[1]).read_text())
cuenta = configured_agents(profile)['cuenta']
allowed = set(cuenta["tools"].get("alsoAllow", []))
denied = set(cuenta["tools"].get("deny", []))
skill = Path(sys.argv[2]).read_text()

assert "message" in denied, "Cuenta must explicitly deny OpenClaw's generic message tool"
assert "owlswatch_telegram_send_message" not in allowed, "Cuenta must have one delivery owner"
assert "Return exactly one final" in skill, "Cuenta skill must require one final response"
PY

echo "Cuenta smoke passed."
