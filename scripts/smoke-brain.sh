#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/tools/brain_intake/server.py"

python3 -m py_compile "$SERVER"
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n' | python3 "$SERVER" >/tmp/owlswatch-brain-tools.json
grep -q 'brain_submit_telegram_update' /tmp/owlswatch-brain-tools.json
grep -q 'brain_health_check' /tmp/owlswatch-brain-tools.json
rm -f /tmp/owlswatch-brain-tools.json

echo "Brain smoke passed."
