# Cobros

Cobros drafts Owl's Watch cuentas de cobro.

It is separate from Cotiza because quotes are pre-sale and cuentas de cobro are post-stay accounting/tax-document work.

## Flow

1. Search/read a targeted Gmail thread or receive pasted Telegram request.
2. Prepare and validate legal/accounting fields.
3. Create a Google Doc from the active template.
4. Export and upload a PDF.
5. Create a Gmail draft reply with the PDF attached.
6. Submit an Operations Email Desk review task.
7. Send a short Telegram alert.

Cobros never sends the final email.

## Runtime Config

Configured values live in `~/.openclaw-owlswatch/openclaw.json` under `mcp.servers.owlswatch_cobros.env`.

Required when live:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `OWLSWATCH_GMAIL_ACCOUNT=info@owlswatch.com`
- `OWLSWATCH_COBROS_FOLDER_ID`
- `OWLSWATCH_COBROS_TEMPLATE_DOC_ID`
- `EMAIL_AGENT_API_TOKEN_FILE`

Optional:

- `OWLSWATCH_COBROS_NOTIFY_CHAT_ID`
- `OWLSWATCH_COBROS_NOTIFY_THREAD_ID`

Use the existing Owl's Watch Ops Telegram group and create a `Cuentas de Cobro` topic for routing.

Generated documents should go in the Google Drive `AI/Cuentas de Cobro` folder. The Google service account must be shared on that folder before live packet generation is enabled.
