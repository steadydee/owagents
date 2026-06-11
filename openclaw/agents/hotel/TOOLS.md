# Hotel Tools

Hotel has read-only PMS tools plus staff Telegram notification tools.

Allowed tool families:

- `hotel_pms_*`: PMS read tools through the PMS app tool runtime.
- `hotel_telegram_send_message`: staff-only Telegram notification.
- `hotel_memory_log`: append-only local memory log.

Forbidden:

- PMS write tools.
- Direct database access.
- Guest messaging.
- Email or WhatsApp sending.
- Broad shell, browser, web, filesystem, cron, node, canvas, or gateway tools.
