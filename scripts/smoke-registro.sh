#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/tools/registro_compliance/server.py"

python3 -m py_compile "$SERVER"
PYTHONPATH="$ROOT" python3 -m unittest discover -s "$ROOT/tools/registro_compliance/tests" -p 'test_*.py'

tools_json="$(python3 "$SERVER" list)"
for tool in \
  registro_list_pending \
  registro_get \
  registro_fetch_media \
  registro_parse_mrz \
  registro_extract_document_vision \
  registro_delete_media \
  registro_record_extraction \
  registro_set_status \
  registro_flag_exception \
  registro_record_submission \
  registro_request_guest_fix \
  registro_telegram_notify
do
  printf '%s\n' "$tools_json" | grep -q "\"$tool\"" || {
    echo "Missing tool: $tool" >&2
    exit 1
  }
done

echo "Registro smoke passed."
