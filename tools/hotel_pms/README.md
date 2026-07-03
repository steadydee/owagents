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
- `hotel_registro_get_by_reservation`
- `hotel_registro_list_guests`
- `hotel_registro_list_documents`
- `hotel_registro_extract_reservation`
- `hotel_registro_prepare_submissions`
- `hotel_registro_prepare_government_submission`
- `hotel_registro_submit_government`
- `hotel_registro_daily_pickup`
- `hotel_registro_record_submission_status`
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

## Registro / Government Submission Boundary

`hotel_registro_extract_reservation` fetches guest document files through
scoped PMS Registro tools, extracts identity fields, and records guest-level
extraction results back to PMS.

`hotel_registro_prepare_submissions` checks whether the PMS Registro record is
validated and ready for due TRA/SIRE submission types. It returns only a
staff-safe staged plan; it does not submit to government systems.

`hotel_registro_prepare_government_submission` calls the PMS-owned
`registro_prepare_government_submission` tool for each due/requested submission
type. PMS prepares the official payload; the Hotel wrapper returns only
staff-safe metadata and never exposes the payload, guest identity fields, file
bytes, or fetch tokens to the model.

`hotel_registro_submit_government` is the only path that can attempt a live
government submission. In `dry_run` mode it verifies readiness only. In `submit`
mode it requires `REGISTRO_GOVERNMENT_SUBMITTER_ENABLED=1`; TRA also requires
a configured official PMS API token from `https://pms.mincit.gov.co/token/`.
The official adapter posts the primary guest to `/one/` and accompanying guests
to `/two/` using the primary guest `code` as `padre`. Until the token is
available, the runtime includes a conservative TRA manual-form adapter that can
only mark submitted after the registered guest is visible in TRA. SIRE remains
blocked until its adapter is verified. The tool records `submitted` in PMS only
after receiving a real receipt/reference or a verified TRA registration-table
match.

`hotel_registro_record_submission_status` can record `pending`, `failed`, or
`needs_info` status for a TRA/SIRE attempt in PMS. It deliberately rejects
`submitted`; submitted status is reserved for the receipt-gated submitter.

`hotel_registro_daily_pickup` is the scheduled operations wrapper. It reads
PMS `registro_list_pending`, limits the normal sweep to yesterday through two
days ahead, extracts uploaded documents, submits TRA only when PMS says the
record is ready, never submits SIRE, and can send one staff-safe Telegram
summary.

Never expose document numbers, fetch tokens, file bytes, base64, or raw OCR in
Telegram or agent memory.

## Runtime Env

- `PMS_BASE_URL`
- `PMS_PROPERTY_ID`
- `OW_AGENT_TOKEN_SECRET` or `OW_AGENT_TOKEN_SECRET_FILE`
- `HOTEL_TELEGRAM_BOT_TOKEN`
- `HOTEL_TELEGRAM_NOTIFY_CHAT_ID`
- `HOTEL_TELEGRAM_NOTIFY_THREAD_ID` optional
- `REGISTRO_GOVERNMENT_SUBMITTER_ENABLED` optional, default `0`
- `TRA_API_BASE_URL` optional, defaults to `https://pms.mincit.gov.co`
- `TRA_API_ONE_URL` optional, defaults to `${TRA_API_BASE_URL}/one/`
- `TRA_API_TWO_URL` optional, defaults to `${TRA_API_BASE_URL}/two/`
- `TRA_SUBMISSION_URL` or `TRA_API_URL` optional custom compatibility endpoint
- `TRA_API_TOKEN` or `TRA_API_TOKEN_FILE` optional official PMS API token
- `TRA_ESTABLISHMENT_NAME` optional, defaults to `Owl's Watch`
- `TRA_RNT_ESTABLISHMENT` optional RNT sent to the official PMS API
- `TRA_LOGIN_URL` optional, defaults to `https://tra.mincit.gov.co/login/`
- `TRA_NEW_GUEST_URL` optional, defaults to `https://tra.mincit.gov.co/padd/`
- `TRA_REGISTERED_GUESTS_URL` optional, defaults to `https://tra.mincit.gov.co/blo`
- `TRA_USERNAME`/`TRA_RNT` or `TRA_USERNAME_FILE`/`TRA_RNT_FILE` optional
- `TRA_PASSWORD` or `TRA_PASSWORD_FILE` optional
- `SIRE_LOGIN_URL` optional

Tokens and secrets are runtime-only. Do not commit them.
