# brain_intake MCP Server

Narrow stdio MCP server and OpenClaw plugin bridge for Brain Telegram capture.

The server owns these side effects:

- Submit text updates to the Brain Intake API.
- Send the Brain receipt back to Telegram.

Tokens are read from environment variables first, then from the runtime OpenClaw profile. Tokens and chat IDs are never accepted as tool parameters except the destination chat id needed to reply, and are never returned in results.

## Config Inputs

- `BRAIN_API_BASE_URL`, default `http://127.0.0.1:3000`
- `BRAIN_API_TOKEN`, preferred for hosted Brain API submissions
- `BRAIN_ADMIN_TOKEN`, fallback only for local/admin-token protected Brain
- `TELEGRAM_BOT_TOKEN`, usually read from `channels.telegram.botToken`
- `OPENCLAW_CONFIG_PATH`, default `~/.openclaw-owlswatch/openclaw.json`

Hosted Brain should use a source-specific Brain token, such as the value from `OPENCLAW_BRAIN_TOKEN`, configured in runtime-only OpenClaw env as `BRAIN_API_TOKEN`. Do not use the dashboard admin token for Telegram routing unless no narrower token exists.
