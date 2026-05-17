#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/tools/owlswatch_quotes/server.py"

python3 -m py_compile "$SERVER"
ROOT="$ROOT" python3 - <<'PY'
import importlib.util
import os
import traceback
from pathlib import Path

root = Path(os.environ["ROOT"])
paths = [
    root / "tools/owlswatch_quotes/tests/test_quote_normalization.py",
    root / "tools/owlswatch_quotes/tests/test_historical_quote_scenarios.py",
]
failures = []
count = 0
for path in paths:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    for name in sorted(dir(module)):
        candidate = getattr(module, name)
        if name.startswith("test_") and callable(candidate):
            count += 1
            try:
                candidate()
            except Exception:
                failures.append((path, name, traceback.format_exc()))
if failures:
    for path, name, tb in failures:
        print(f"FAIL {path}::{name}")
        print(tb)
    raise SystemExit(1)
print(f"Ran {count} Cotiza test functions.")
PY
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n' | python3 "$SERVER" >/tmp/owlswatch-cotiza-tools.json
grep -q 'owlswatch_quote_create_draft' /tmp/owlswatch-cotiza-tools.json
grep -q 'owlswatch_quote_revise_draft' /tmp/owlswatch-cotiza-tools.json
rm -f /tmp/owlswatch-cotiza-tools.json

echo "Cotiza smoke passed."
