#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/tools/hotel_pms/server.py"

python3 -m py_compile "$SERVER"

tools_json="$(python3 "$SERVER" list)"
for tool in \
  hotel_pms_get_tomorrow_arrivals \
  hotel_pms_list_arrivals \
  hotel_pms_find_reservation \
  hotel_pms_get_reservation_context \
  hotel_pms_get_dashboard_snapshot \
  hotel_pms_get_lifecycle_snapshot \
  hotel_telegram_send_message \
  hotel_memory_log
do
  printf '%s\n' "$tools_json" | grep -q "\"$tool\"" || {
    echo "Missing tool: $tool" >&2
    exit 1
  }
done

echo "Hotel smoke passed."
