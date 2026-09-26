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
  a staff-safe summary plus a native confirmation command. It does not create a
  reservation.
- `hotel_pms_create_reservation`: returns an already confirmed result. It
  cannot approve a pending draft. Only the authenticated native command
  `/confirmar_reserva <reference>` authorizes creation, bound to the preparer,
  conversation/topic, payload hash, and expiry.

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
- Claiming government submission without a verified receipt. Use only
  `hotel_registro_submit_government`; unknown outcomes block blind replay.
- Reservation update, cancel, delete, checklist, guest-message, finance, admin,
  or arbitrary PMS write tools.
- Direct database access.
- Guest messaging.
- Email or WhatsApp sending.
- Broad shell, browser, web, filesystem, cron, node, canvas, or gateway tools.

Government submission tools journal each external step before writing. A known
receipt with a PMS write failure can be retried without resubmission. Unknown
outcomes require portal/PMS reconciliation. Telegram notifications are pinned
to the configured staff chat and topic; destination overrides are rejected.
