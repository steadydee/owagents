# Correo Integration Notes

Correo is Gmail-first.

Correo scans Gmail, drafts replies directly in Gmail, sends concise Telegram
notifications, and keeps local task files only for de-duplication/recovery.

Correo does not write email draft tasks, scan runs, or review records to
Operations. Operations remains useful for expenses, quotes, PMS-related systems,
and cuentas de cobro, but not as the email review surface.

Correo should never call Gmail send.

## Runtime Secrets

The Mac mini needs these runtime values. Do not commit them.

- `OW_AGENT_TOKEN_SECRET`
- `GOOGLE_APPLICATION_CREDENTIALS`

## Gmail Scopes

Read-only scan requires:

`https://www.googleapis.com/auth/gmail.readonly`

Gmail draft creation requires:

`https://www.googleapis.com/auth/gmail.compose`

Enable `OWLSWATCH_GMAIL_DRAFTS_ENABLED=1` only after compose scope is approved.
Humans review, edit, and send directly in Gmail.

## Scheduling

Use the install script after Gmail read and compose scopes are configured:

```sh
./scripts/install-email-schedules.sh
```

This installs:

- 30-minute polling scan
- daily summary
- daily unanswered scan

The jobs call `openclaw agent --agent correo`; Correo sends Telegram notifications itself through narrow tools.
