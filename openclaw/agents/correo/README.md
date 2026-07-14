# Correo

Correo is the Owl's Watch email drafting agent.

Correo scans Owl's Watch Gmail, identifies important operational email threads, uses Luna for approved guest-shareable context, and creates Gmail draft replies for human review.

Correo does not send email. Humans review, edit, and send in Gmail.

## Jobs

Correo supports three operating modes:

- 30-minute polling scan for new important emails
- daily important-email summary
- unanswered-email scan for threads from the last week where the latest meaningful message appears to be from the client/operator/vendor

## Runtime Config

Configured values live in `~/.openclaw-owlswatch/openclaw.json` under `mcp.servers.owlswatch_email.env`.

Required when live:

- `OW_AGENT_TOKEN_SECRET`
- `LUNA_BASE_URL=https://luna.owlswatch.com`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `OWLSWATCH_GMAIL_ACCOUNT=info@owlswatch.com`
- `OWLSWATCH_GMAIL_DRAFTS_ENABLED=1`

Optional:

- `OWLSWATCH_EMAIL_NOTIFY_CHAT_ID`
- `OWLSWATCH_EMAIL_NOTIFY_THREAD_ID`

Use the existing Owl's Watch Ops Telegram group and `owbot` by default. Create an `Email` topic, then set `OWLSWATCH_EMAIL_NOTIFY_THREAD_ID` to that topic id before enabling schedules.

Gmail draft creation requires the Gmail compose scope. If Gmail draft creation is disabled or fails, Correo should not announce a normal draft-ready alert; it should report the blocker or create local recovery state only.

## Boundaries

Gmail is the review and send system for email drafts. Correo does not write
email tasks or scan runs to Operations. Quote drafts live in Google Drive, and
Operations remains the source of truth for expenses and cuentas de cobro. Luna is
only a context provider.

Correo never auto-sends.
