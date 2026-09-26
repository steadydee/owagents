#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_DIR="$(mktemp -d)"
trap 'rm -rf "$SMOKE_DIR"' EXIT
export PYTHONPYCACHEPREFIX="$SMOKE_DIR/pycache"
export OWLSWATCH_COTIZA_WORKSPACE="$SMOKE_DIR/cotiza"
python3 -m unittest discover -s "$ROOT/tests" -p 'test_*.py'
node --test "$ROOT"/tools/agent_runtime_guard/tests/*.test.mjs
node --test "$ROOT"/tools/owlswatch_cobros/tests/*.test.mjs
if compgen -G "$ROOT/tools/hotel_pms/tests/*.test.mjs" > /dev/null; then
  node --test "$ROOT"/tools/hotel_pms/tests/*.test.mjs
fi
python3 -m unittest discover -s "$ROOT/tools/hotel_pms/tests" -p 'test_*.py'
for name in cuenta cotiza correo cobros hotel finca registro; do
  "$ROOT/scripts/smoke-$name.sh"
done
"$ROOT/scripts/test-telegram-observer.sh"
"$ROOT/scripts/check-no-secrets.sh"
