# Owl's Watch Email Tools

This plugin exposes narrow tools for Correo, the Owl's Watch email drafting agent.

The tools are intentionally scoped:

- Gmail tools are read-only unless Gmail draft creation is explicitly enabled.
- Luna is called only through `get_email_response_context` with a scoped machine token.
- Operations Email Desk writes go only through `/api/emails/intake` and `/api/emails/scan-runs`.
- Telegram is used only for operational notifications.
- No tool sends final email.

Required runtime values live in `~/.openclaw-owlswatch/openclaw.json` under `mcp.servers.owlswatch_email.env` or in the process environment.

Required for read-only scan:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `OWLSWATCH_GMAIL_ACCOUNT`

Required for Luna context:

- `OW_AGENT_TOKEN_SECRET`
- `LUNA_BASE_URL`

Required for Operations Email Desk:

- `OPERATIONS_BASE_URL`
- `EMAIL_AGENT_API_TOKEN_FILE=~/.openclaw-owlswatch/secrets/email-agent-token.tmp`

Optional:

- `OWLSWATCH_EMAIL_NOTIFY_CHAT_ID`
- `OWLSWATCH_EMAIL_NOTIFY_THREAD_ID`
- `OWLSWATCH_GMAIL_DRAFTS_ENABLED=1`

Gmail draft creation also requires the Workspace domain-wide delegation scope:

`https://www.googleapis.com/auth/gmail.compose`

Do not enable Gmail drafts until Operations review/send boundaries are ready.
