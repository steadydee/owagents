#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/tools/owlswatch_intake/server.py"

python3 -m py_compile "$SERVER"
python3 -m unittest discover -s "$ROOT/tools/owlswatch_intake/tests" -p 'test_*.py'
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n' | python3 "$SERVER" >/tmp/owlswatch-cuenta-tools.json
grep -q 'owlswatch_operations_create_expense_draft' /tmp/owlswatch-cuenta-tools.json
grep -q 'owlswatch_album_buffer_check' /tmp/owlswatch-cuenta-tools.json
rm -f /tmp/owlswatch-cuenta-tools.json

echo "Cuenta smoke passed."
