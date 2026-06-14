# Owl's Watch Email Tools

This plugin exposes narrow tools for Correo, the Owl's Watch email drafting agent.

The tools are intentionally scoped:

- Gmail tools are read-only unless Gmail draft creation is explicitly enabled.
- Luna is called only through `get_email_response_context` with a scoped machine token.
- Local task files are used for de-duplication and recovery.
- Telegram is used only for operational notifications.
- No tool sends final email.

Required runtime values live in `~/.openclaw-owlswatch/openclaw.json` under `mcp.servers.owlswatch_email.env` or in the process environment.

Required for read-only scan:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `OWLSWATCH_GMAIL_ACCOUNT`

Required for Luna context:

- `OW_AGENT_TOKEN_SECRET`
- `LUNA_BASE_URL`

Optional:

- `OWLSWATCH_EMAIL_NOTIFY_CHAT_ID`
- `OWLSWATCH_EMAIL_NOTIFY_THREAD_ID`
- `OWLSWATCH_GMAIL_DRAFTS_ENABLED=1`

Gmail draft creation also requires the Workspace domain-wide delegation scope:

`https://www.googleapis.com/auth/gmail.compose`

Correo creates Gmail drafts only. Humans review, edit, and send in Gmail.
