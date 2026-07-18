# Finca Tools

Use only:

- `finca_tasks_list`
- `finca_tasks_get`
- `finca_tasks_create`
- `finca_tasks_update`
- `finca_tasks_attach_photos`
- `finca_tasks_send_daily_report`

The tools own the Operations property, app credential, durable spool paths,
idempotency, and deterministic report delivery.

`finca_telegram_send_message` exists only for external schedule scripts. It is
not exposed to the model and must never be used for an inbound Telegram reply.

Broad shell, browser, web, filesystem, gateway, node, canvas, cron, finance, and payroll tools are forbidden.
