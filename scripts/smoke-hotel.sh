#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/tools/hotel_pms/server.py"
PROFILE="$ROOT/openclaw/profiles/hotel/openclaw.example.json"

python3 -m py_compile "$SERVER"
python3 -m py_compile "$ROOT/scripts/observe-telegram-channel.py"
"$ROOT/scripts/test-telegram-observer.sh"

tools_json="$(python3 "$SERVER" list)"
for tool in \
  hotel_pms_get_tomorrow_summary \
  hotel_pms_get_tomorrow_arrivals \
  hotel_pms_list_arrivals \
  hotel_pms_list_departures \
  hotel_pms_list_in_house \
  hotel_pms_find_reservation \
  hotel_pms_get_reservation_context \
  hotel_pms_get_dashboard_snapshot \
  hotel_pms_get_lifecycle_snapshot \
  hotel_pms_prepare_reservation \
  hotel_pms_create_reservation \
  hotel_registro_prepare_government_submission \
  hotel_registro_submit_government \
  hotel_registro_daily_pickup \
  hotel_telegram_send_message \
  hotel_memory_log
do
  printf '%s\n' "$tools_json" | grep -q "\"$tool\"" || {
    echo "Missing tool: $tool" >&2
    exit 1
  }
done

python3 - "$PROFILE" <<'PY'
import json
import sys
from pathlib import Path

profile = json.loads(Path(sys.argv[1]).read_text())
mcp_server = profile["mcp"]["servers"]["hotel_pms"]
plugins = profile["plugins"]["entries"]

assert mcp_server.get("enabled") is False, "legacy Hotel MCP runner must stay disabled"
assert plugins.get("hotel-pms", {}).get("enabled") is True, "native Hotel plugin must stay enabled"
PY

echo "Hotel smoke passed."
