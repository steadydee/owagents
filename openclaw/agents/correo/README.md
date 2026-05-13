# Correo

Correo is the Owl's Watch email drafting agent.

Correo scans Owl's Watch Gmail, identifies important operational email threads, uses Luna for approved guest-shareable context, and creates reviewable email draft tasks for Operations Email Desk.

Correo does not send email. Humans review and send through Operations/Gmail.

## Jobs

Correo supports three operating modes:

- 30-minute polling scan for new important emails
- daily important-email summary
- unanswered-email scan for threads from the last week where the latest meaningful message appears to be from the client/operator/vendor

## Runtime Config

Configured values live in `~/.openclaw-owlswatch/openclaw.json` under `mcp.servers.owlswatch_email.env`.

Required when live:

- `OW_AGENT_TOKEN_SECRET`
- `EMAIL_AGENT_API_TOKEN_FILE=~/.openclaw-owlswatch/secrets/email-agent-token.tmp`
- `OPERATIONS_BASE_URL=https://operations.owlswatch.com`
- `LUNA_BASE_URL=https://luna.owlswatch.com`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `OWLSWATCH_GMAIL_ACCOUNT=info@owlswatch.com`

Optional:

- `OWLSWATCH_EMAIL_NOTIFY_CHAT_ID`
- `OWLSWATCH_EMAIL_NOTIFY_THREAD_ID`
- `OWLSWATCH_GMAIL_DRAFTS_ENABLED=1`

Use the existing Owl's Watch Ops Telegram group and `owbot` by default. Create an `Email` topic, then set `OWLSWATCH_EMAIL_NOTIFY_THREAD_ID` to that topic id before enabling schedules.

Gmail draft creation requires the Gmail compose scope. Until that scope is approved and the config flag is enabled, Correo creates Operations/local review tasks only.

## Boundaries

Operations Email Desk is the review desk and audit trail. Gmail remains the thread and send system. Luna is only a context provider.

Correo never auto-sends.
