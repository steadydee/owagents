#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/tools/owlswatch_quotes/server.py"

python3 -m py_compile "$SERVER"
python3 "$ROOT/tools/owlswatch_quotes/tests/test_quote_normalization.py"
python3 "$ROOT/tools/owlswatch_quotes/tests/test_historical_quote_scenarios.py"
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n' | python3 "$SERVER" >/tmp/owlswatch-cotiza-tools.json
grep -q 'owlswatch_quote_create_draft' /tmp/owlswatch-cotiza-tools.json
grep -q 'owlswatch_quote_revise_draft' /tmp/owlswatch-cotiza-tools.json
rm -f /tmp/owlswatch-cotiza-tools.json

echo "Cotiza smoke passed."

