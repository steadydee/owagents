# Hotel PMS Tools

OpenClaw tools for the Hotel operations agent.

The tools call the PMS app tool runtime using short-lived HMAC machine tokens.
They do not connect to the PMS database directly and they do not send messages
to guests. Most tools are read-only; reservation creation is guarded by a
two-step PMS-prepared token and simple staff `sí` confirmation.

## Tools

- `hotel_pms_get_tomorrow_arrivals`
- `hotel_pms_get_tomorrow_summary`
- `hotel_pms_list_arrivals`
- `hotel_pms_list_departures`
- `hotel_pms_list_in_house`
- `hotel_pms_list_reservations`
- `hotel_pms_find_reservation`
- `hotel_pms_get_reservation_context`
- `hotel_pms_get_dashboard_snapshot`
- `hotel_pms_get_lifecycle_snapshot`
- `hotel_pms_list_booking_revisions`
- `hotel_pms_list_sync_events`
- `hotel_pms_get_mapping_status`
- `hotel_pms_get_ari_outbox_health`
- `hotel_pms_prepare_reservation`
- `hotel_pms_create_reservation`
- `hotel_telegram_send_message`
- `hotel_memory_log`

## Reservation Creation Boundary

`hotel_pms_prepare_reservation` calls the PMS-owned `agent_prepare_reservation`
tool with a prepare-only token. It stores the returned `preparedToken` in the
workspace spool and returns only a staff-safe summary plus hidden pending id.

`hotel_pms_create_reservation` loads the pending prepared token by hidden
pending id after staff replies `sí`, then calls the PMS-owned
`agent_create_reservation` tool with a guarded write token. It never accepts an
arbitrary prepared payload from the model. Legacy explicit code confirmation is
still accepted for backwards compatibility.

The Hotel agent must never expose prepared tokens, payload hashes, prices,
balances, deposits, or payment details.

## Runtime Env

- `PMS_BASE_URL`
- `PMS_PROPERTY_ID`
- `OW_AGENT_TOKEN_SECRET` or `OW_AGENT_TOKEN_SECRET_FILE`
- `HOTEL_TELEGRAM_BOT_TOKEN`
- `HOTEL_TELEGRAM_NOTIFY_CHAT_ID`
- `HOTEL_TELEGRAM_NOTIFY_THREAD_ID` optional

Tokens and secrets are runtime-only. Do not commit them.
