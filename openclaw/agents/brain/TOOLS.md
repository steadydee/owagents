# Tools

Use only configured Brain tools:

- `brain_submit_telegram_update`
- `brain_submit_intake`
- `brain_health_check`
- `brain_telegram_send_message`

The main workflow should use `brain_submit_telegram_update`, which submits the update to Brain and sends the receipt back to Telegram.

Do not use broad filesystem, shell, browser, web, gateway, cron, or database tools.
