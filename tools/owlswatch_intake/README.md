# owlswatch_intake MCP Server

Narrow stdio MCP server for Cuenta receipt intake.

The server owns all external side effects:
- Telegram Bot API `getFile`, file download, and direct reply.
- Durable album buffering and spool paths.
- Operations attachment upload and expense draft creation.
- Vision receipt extraction through a configured provider.
- Cuenta memory append.

Tokens are read from environment variables first, then from the owlswatch OpenClaw profile config. Tokens are never accepted as tool parameters and are never returned in results.

Receipt normalization is deterministic at the tool boundary:

- categories are converted to the canonical Operations category names;
- transfer recipients are preferred over payment rails as vendors;
- captions, OCR, confidence, and actionable flags are preserved in the intake payload;
- missing tax/subtotal/payment metadata and provider provenance do not create review noise.

## Config Inputs

Preferred environment/config names:

- `TELEGRAM_BOT_TOKEN`
- `EXPENSE_INTAKE_API_TOKEN`
- `OPERATIONS_API_BASE_URL`
- `OWLSWATCH_VISION_API_KEY`
- `OWLSWATCH_VISION_ENDPOINT`
- `OWLSWATCH_VISION_MODEL`

OpenClaw passes these through `mcp.servers.owlswatch_intake.env` or `env.vars`.
