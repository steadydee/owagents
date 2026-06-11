# Hotel PMS Tools

Read-only OpenClaw tools for the Hotel operations agent.

The tools call the PMS app tool runtime using a short-lived HMAC machine token.
They do not connect to the PMS database directly and they do not send messages
to guests.

## Tools

- `hotel_pms_get_tomorrow_arrivals`
- `hotel_pms_list_arrivals`
- `hotel_pms_find_reservation`
- `hotel_pms_get_reservation_context`
- `hotel_pms_get_dashboard_snapshot`
- `hotel_pms_get_lifecycle_snapshot`
- `hotel_telegram_send_message`
- `hotel_memory_log`

## Runtime Env

- `PMS_BASE_URL`
- `PMS_PROPERTY_ID`
- `OW_AGENT_TOKEN_SECRET` or `OW_AGENT_TOKEN_SECRET_FILE`
- `HOTEL_TELEGRAM_BOT_TOKEN`
- `HOTEL_TELEGRAM_NOTIFY_CHAT_ID`
- `HOTEL_TELEGRAM_NOTIFY_THREAD_ID` optional

Tokens and secrets are runtime-only. Do not commit them.
