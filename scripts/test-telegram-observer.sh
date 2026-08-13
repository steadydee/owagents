#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/state/agents/main/sessions" "$TMP/state/telegram/ingress-spool-default"
cat > "$TMP/healthy.json" <<'JSON'
{
  "channels": {"telegram": {"configured": true, "running": true, "probe": {"ok": true}}},
  "channelAccounts": {"telegram": [{"connected": true, "restartPending": false, "lastError": null}]}
}
JSON
cat > "$TMP/unhealthy.json" <<'JSON'
{
  "channels": {"telegram": {"configured": true, "running": false, "probe": {"ok": false}}},
  "channelAccounts": {"telegram": [{"connected": false, "restartPending": true, "lastError": "fixture"}]}
}
JSON
cat > "$TMP/state/agents/main/sessions/sessions.json.telegram-messages.json" <<'JSONL'
{"key":"fixture","node":{"sourceMessage":{"message_id":42,"from":{"id":7},"chat":{"id":-99},"date":1700000000,"text":"must not be journaled"}}}
JSONL
python3 - "$TMP/state/state/openclaw.sqlite" <<'PY'
import json
import os
import sqlite3
import sys

path = sys.argv[1]
os.makedirs(os.path.dirname(path), exist_ok=True)
db = sqlite3.connect(path)
db.execute("""CREATE TABLE plugin_state_entries (
  plugin_id TEXT NOT NULL,
  namespace TEXT NOT NULL,
  entry_key TEXT NOT NULL,
  value_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER,
  PRIMARY KEY (plugin_id, namespace, entry_key)
)""")
db.execute("""CREATE TABLE channel_ingress_events (
  queue_name TEXT NOT NULL, event_id TEXT NOT NULL, channel_id TEXT NOT NULL,
  account_id TEXT NOT NULL, status TEXT NOT NULL, received_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL, failed_at INTEGER,
  PRIMARY KEY (queue_name, event_id)
)""")
value = {"sourceMessage": {"message_id": 43, "from": {"id": 8}, "chat": {"id": -99}, "date": 1700000001, "text": "also private"}}
db.execute("INSERT INTO plugin_state_entries VALUES (?,?,?,?,?,NULL)", ("telegram", "telegram.message-cache", "fixture:43", json.dumps(value), 1700000001000))
db.commit()
db.close()
PY

run_observer() {
  "$ROOT/scripts/observe-telegram-channel.py" \
    --profile hotel \
    --state-dir "$TMP/state" \
    --status-json "$1" \
    --dry-run >/dev/null || true
}

set_outage_age() {
  python3 - "$TMP/state/monitor/telegram-health-state.json" "$1" <<'PY'
import json
import sys
import time

path, age = sys.argv[1], float(sys.argv[2])
with open(path, encoding="utf-8") as handle:
    state = json.load(handle)
state["outageStartedAtEpoch"] = time.time() - age
with open(path, "w", encoding="utf-8") as handle:
    json.dump(state, handle)
    handle.write("\n")
PY
}

run_observer "$TMP/healthy.json"
grep -q '"health": "healthy"' "$TMP/state/logs/telegram-health.jsonl"
grep -q '"messageId": 42' "$TMP/state/logs/telegram-ingress-journal.jsonl"
grep -q '"messageId": 43' "$TMP/state/logs/telegram-ingress-journal.jsonl"
if grep -q 'must not be journaled' "$TMP/state/logs/telegram-ingress-journal.jsonl"; then
  echo "Observer leaked Telegram message text" >&2
  exit 1
fi
if grep -q 'also private' "$TMP/state/logs/telegram-ingress-journal.jsonl"; then
  echo "Observer leaked SQLite Telegram message text" >&2
  exit 1
fi

run_observer "$TMP/unhealthy.json"
run_observer "$TMP/unhealthy.json"
tail -1 "$TMP/state/logs/telegram-health.jsonl" | grep -q '"health": "suspect"'
tail -1 "$TMP/state/logs/telegram-health.jsonl" | grep -q '"alertKind": null'

# A brief interruption recovers silently because no failure alert was sent.
run_observer "$TMP/healthy.json"
tail -1 "$TMP/state/logs/telegram-health.jsonl" | grep -q '"alertKind": null'

# A sustained interruption alerts once. Recovery is announced only after the
# corresponding failure alert was successfully delivered.
run_observer "$TMP/unhealthy.json"
set_outage_age 601
run_observer "$TMP/unhealthy.json"
tail -1 "$TMP/state/logs/telegram-health.jsonl" | grep -q '"health": "unhealthy"'
tail -1 "$TMP/state/logs/telegram-health.jsonl" | grep -q '"alertKind": "failure"'

run_observer "$TMP/healthy.json"
tail -1 "$TMP/state/logs/telegram-health.jsonl" | grep -q '"alertKind": "recovery"'

if rg -n 'getUpdates|gateway restart|launchctl kickstart' "$ROOT/scripts/observe-telegram-channel.py"; then
  echo "Observer must not poll Telegram or restart OpenClaw" >&2
  exit 1
fi

echo "Telegram observer tests passed."
