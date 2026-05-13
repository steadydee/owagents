# Correo Integration Notes

Correo depends on the Operations Email Desk contract.

Expected endpoints:

- `POST /api/emails/intake`
- `POST /api/emails/scan-runs`

Optional future endpoints used by Operations UI, not directly required by Correo:

- `GET /api/emails/tasks`
- `GET /api/emails/tasks/:id`
- `PATCH /api/emails/tasks/:id`
- `POST /api/emails/tasks/:id/create-gmail-draft`
- `POST /api/emails/tasks/:id/update-gmail-draft`
- `POST /api/emails/tasks/:id/send`

Correo should never call send.

## Runtime Secrets

The Mac mini needs these runtime values. Do not commit them.

- `OW_AGENT_TOKEN_SECRET`
- `EMAIL_AGENT_API_TOKEN_FILE=~/.openclaw-owlswatch/secrets/email-agent-token.tmp`
- `GOOGLE_APPLICATION_CREDENTIALS`

## Gmail Scopes

Read-only scan requires:

`https://www.googleapis.com/auth/gmail.readonly`

Gmail draft creation requires:

`https://www.googleapis.com/auth/gmail.compose`

Do not enable `OWLSWATCH_GMAIL_DRAFTS_ENABLED=1` until compose scope is approved and Operations review/send boundaries are live.

## Scheduling

Use the install script only after Operations Email Desk is ready:

```sh
./scripts/install-email-schedules.sh
```

This installs:

- 30-minute polling scan
- daily summary
- daily unanswered scan

The jobs call `openclaw agent --agent correo`; Correo sends Telegram notifications itself through narrow tools.
