#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/tools/finca_tasks/server.py"

python3 -m py_compile "$SERVER"
python3 -m unittest discover -s "$ROOT/tools/finca_tasks/tests" -v

tools_json="$(python3 "$SERVER" list)"
for tool in \
  finca_tasks_list \
  finca_tasks_get \
  finca_tasks_create \
  finca_tasks_update \
  finca_tasks_attach_photos \
  finca_tasks_send_daily_report \
  finca_telegram_send_message
do
  printf '%s\n' "$tools_json" | grep -q "\"$tool\"" || {
    echo "Missing tool: $tool" >&2
    exit 1
  }
done

grep -q '"FINCA_TASKS_MOCKS": "0"' "$ROOT/openclaw/profiles/finca/openclaw.example.json"
grep -q '"cron"' "$ROOT/openclaw/profiles/finca/openclaw.example.json"

echo "Finca smoke passed."
