#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/tools/owlswatch_email/server.py"

python3 -m py_compile "$SERVER"
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n' | python3 "$SERVER" >/tmp/owlswatch-correo-tools.json
grep -q 'owlswatch_email_search_recent_threads' /tmp/owlswatch-correo-tools.json
grep -q 'owlswatch_luna_get_email_response_context' /tmp/owlswatch-correo-tools.json
grep -q 'owlswatch_email_submit_operations_intake' /tmp/owlswatch-correo-tools.json
rm -f /tmp/owlswatch-correo-tools.json

echo "Correo smoke passed."
