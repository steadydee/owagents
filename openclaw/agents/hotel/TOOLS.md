# Hotel Tools

Hotel has PMS read tools, guarded reservation creation tools, Registro
extraction tools, and staff Telegram notification tools.

Allowed tool families:

- `hotel_pms_*`: PMS tools through the PMS app tool runtime.
- `hotel_registro_*`: PMS Registro extraction tools. These never expose raw
  document bytes or fetch tokens to the model.
- `hotel_telegram_send_message`: staff-only Telegram notification.
- `hotel_memory_log`: append-only local memory log.

Reservation creation tools:

- `hotel_pms_prepare_reservation`: validates normalized staff intent and returns
  a staff-safe summary plus hidden pending confirmation id. It does not create a
  reservation.
- `hotel_pms_create_reservation`: creates only from a pending prepared token
  after staff replies `sí`. It does not accept arbitrary reservation
  payloads.

Registro tools:

- `hotel_registro_get_by_reservation`: reads whether a reservation has a
  Registro record.
- `hotel_registro_list_guests`: lists structured Registro guests.
- `hotel_registro_list_documents`: lists safe document metadata only.
- `hotel_registro_extract_reservation`: fetches scoped documents tool-side,
  calls vision, and records guest-level extraction in PMS.

Hotel PMS tools expose operational PMS context only. Do not use Hotel to answer
finance, pricing, rate, balance, payment, or deposit questions in a worker
Telegram group.

Forbidden:

- PMS write tools other than `hotel_pms_create_reservation`.
- Registro submission tools that claim SIRE/TRA completion. Registro extraction
  is allowed; government submission is not live yet.
- Reservation update, cancel, delete, checklist, guest-message, finance, admin,
  or arbitrary PMS write tools.
- Direct database access.
- Guest messaging.
- Email or WhatsApp sending.
- Broad shell, browser, web, filesystem, cron, node, canvas, or gateway tools.
