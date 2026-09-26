# Hotel PMS Tools

OpenClaw tools for the Hotel operations agent.

The tools call the PMS app tool runtime using short-lived HMAC machine tokens.
They do not connect to the PMS database directly and they do not send messages
to guests. Most tools are read-only; reservation creation is guarded by a
PMS-prepared token and an authenticated native Telegram confirmation command.

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

The native `/confirmar_reserva <reference>` command receives OpenClaw's
`isAuthorizedSender`, sender, agent, session and conversation context directly.
Only an authorized Telegram user who prepared that exact draft in that same
chat/topic/session can approve it. The plugin injects context via a separate
subprocess environment; no model parameter is trusted for authorization.
The draft hash binds the signed PMS payload, confirmation code, request,
expiry, and sender/conversation. A durable exclusive claim permits one create;
replays return the saved result. A crash/timeout after claiming blocks further
writes and directs staff to PMS for reconciliation.

`hotel_pms_create_reservation` is retained for compatibility as a read of a
previously confirmed result. Model `si`, legacy codes, booleans and fabricated
identity fields cannot approve. Prepare without trusted host context returns
an actionable PMS review link. Old drafts without a trusted binding must be
prepared again. The native command is intentionally outside the model catalog.

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
only mark submitted after the registered guest is visible in TRA. SIRE payload
validation is implemented for the official `Alojamiento y Hospedaje` form
contract and can call a configured SIRE adapter endpoint, but live SIRE remains
blocked until that endpoint or browser routine is verified. The tool records
`submitted` in PMS only after receiving a real receipt/reference or a verified
TRA registration-table match.

`hotel_registro_record_submission_status` can record `pending`, `failed`, or
`needs_info` status for a TRA/SIRE attempt in PMS. It deliberately rejects
`submitted`; submitted status is reserved for the receipt-gated submitter.

`hotel_registro_daily_pickup` is the scheduled operations wrapper. It reads
PMS `registro_list_pending`, scans a bounded lookback window for late document
uploads, extracts uploaded documents, submits TRA only when PMS says the record
is ready, never submits SIRE, and can send one staff-safe Telegram summary.
Unchanged review/error alerts are suppressed after their first notification;
changed or recurring issues alert again. The runtime state stores only hashed
issue identifiers and fingerprints, never guest names or document data.

Never expose document numbers, fetch tokens, file bytes, base64, or raw OCR in
Telegram or agent memory.

## Runtime Env

- `PMS_BASE_URL`
- `PMS_PROPERTY_ID`
- `OW_AGENT_TOKEN_SECRET` or `OW_AGENT_TOKEN_SECRET_FILE`
- `HOTEL_TELEGRAM_BOT_TOKEN`
- `HOTEL_TELEGRAM_NOTIFY_CHAT_ID`
- `HOTEL_TELEGRAM_NOTIFY_THREAD_ID` optional
- `HOTEL_REGISTRO_PICKUP_ALERT_STATE` optional; defaults to a private workspace state file
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
- `SIRE_SUBMISSION_URL` or `SIRE_API_URL` optional verified SIRE adapter endpoint
- `SIRE_API_TOKEN` or `SIRE_API_TOKEN_FILE` optional token for the verified SIRE adapter endpoint
- `SIRE_AUTH_SCHEME` optional, defaults to `Bearer`

Tokens and secrets are runtime-only. Do not commit them.

## Durable recovery and routing

`state/government-submissions/` holds private atomic journals (mode 0600),
locked across processes by property/registration/submission type. The first
claim and every external step are fsynced before the write. TRA primary and
companion references are persisted separately. Confirmed steps are reused; a
surviving `sending` or `unknown` step is never blindly repeated. Companion API
responses must explicitly report success; unrecognized responses require review.

A verified provider receipt is persisted before PMS recording. If PMS is
unavailable, the same tool operation retries only receipt recording. This is
`government_receipt_pending`. `government_outcome_unknown` means the provider
may have accepted a write. `government_payload_changed` blocks reuse with
changed data. Both require an operator to inspect the portal and reconcile PMS
and the private journal using actual evidence; never delete a journal or use a
new identifier to force replay. Partial TRA operations retain the primary and
all known companion results for that review. There is no model-facing reset or
reconciliation override. Keep journals across deployments and protect them as
sensitive runtime state. This is one-host locking; run one active submitter per
property until PMS owns a distributed claim.

The tool rejects Telegram chat/topic overrides different from
`HOTEL_TELEGRAM_NOTIFY_CHAT_ID`/`HOTEL_TELEGRAM_NOTIFY_THREAD_ID`. Interactive
replies continue through OpenClaw's authenticated delivery route.

Verification: `python3 -m unittest discover -s tools/hotel_pms/tests` and
`node --test tools/hotel_pms/tests/*.test.mjs`. Tests use synthetic data and
fake I/O, including a killed process after a simulated provider write, concurrent
claims, receipt-only recovery, forged approvals and destination overrides.
