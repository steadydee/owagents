#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/tools/owlswatch_email/server.py"

python3 -m py_compile "$SERVER"
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n' | python3 "$SERVER" >/tmp/owlswatch-correo-tools.json
grep -q 'owlswatch_email_search_recent_threads' /tmp/owlswatch-correo-tools.json
grep -q 'owlswatch_luna_get_email_response_context' /tmp/owlswatch-correo-tools.json
grep -q 'owlswatch_email_upsert_task' /tmp/owlswatch-correo-tools.json
grep -q 'owlswatch_email_create_gmail_draft' /tmp/owlswatch-correo-tools.json
if grep -q 'owlswatch_email_submit_operations_intake' /tmp/owlswatch-correo-tools.json; then
  echo "Retired Operations email-intake tool is still exposed." >&2
  exit 1
fi
grep -q 'maxAgeHours' /tmp/owlswatch-correo-tools.json
rm -f /tmp/owlswatch-correo-tools.json

TMP_WORKSPACE="$(mktemp -d)"
trap 'rm -rf "$TMP_WORKSPACE"' EXIT
OWLSWATCH_EMAIL_WORKSPACE="$TMP_WORKSPACE" SERVER_PATH="$SERVER" python3 - <<'PY'
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path

server_path = Path(os.environ["SERVER_PATH"])
spec = importlib.util.spec_from_file_location("owlswatch_email_server", server_path)
server = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(server)

task_dir = Path(os.environ["OWLSWATCH_EMAIL_WORKSPACE"]) / "tasks" / "email"
task_dir.mkdir(parents=True, exist_ok=True)
now = dt.datetime.now(dt.timezone.utc)
old = now - dt.timedelta(days=5)

(task_dir / "recent.json").write_text(json.dumps({
    "taskId": "recent",
    "status": "needs_human",
    "thread": {"messages": [{"sentAt": now.isoformat().replace("+00:00", "Z")}]},
}), encoding="utf-8")
(task_dir / "old.json").write_text(json.dumps({
    "taskId": "old",
    "status": "needs_human",
    "thread": {"messages": [{"sentAt": old.isoformat().replace("+00:00", "Z")}]},
}), encoding="utf-8")
(task_dir / "missing-date.json").write_text(json.dumps({
    "taskId": "missing-date",
    "status": "needs_human",
    "createdAt": now.isoformat().replace("+00:00", "Z"),
}), encoding="utf-8")

result = server.tool_email_list_open_tasks({
    "statuses": ["needs_human"],
    "maxAgeHours": 24,
    "requireRecentExternal": True,
})
ids = {task["taskId"] for task in result["tasks"]}
assert ids == {"recent"}, ids

assert not server.is_low_value_message({
    "from": "donotreply@app.thebookingbutton.com",
    "subject": "[Little Hotelier] Enquiry received from: Miranda Davies",
    "bodyText": "You have received an enquiry for: Owl's Watch",
})
assert server.is_low_value_message({
    "from": "newsletter@example.com",
    "subject": "Weekly newsletter",
    "bodyText": "unsubscribe",
})
PY

echo "Correo smoke passed."
