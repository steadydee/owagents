# Owl's Watch Cobros Tools

This plugin exposes narrow tools for Cobros, the Owl's Watch cuenta de cobro drafting agent.

The tools are intentionally scoped:

- Gmail search/read is read-only.
- Drive writes are limited to copying the configured cuenta de cobro template, creating the exported PDF, and placing both in the configured Cobros folder.
- Gmail draft creation attaches the generated PDF but never sends.
- Operations integration writes only to Email Desk intake.
- Telegram is used only for operational review alerts.

Required runtime values live in `~/.openclaw-owlswatch/openclaw.json` under `mcp.servers.owlswatch_cobros.env` or in the process environment.

Required:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `GOOGLE_WORKSPACE_IMPERSONATE_USER=info@owlswatch.com`
- `OWLSWATCH_GMAIL_ACCOUNT`
- `OWLSWATCH_COBROS_FOLDER_ID`
- `OWLSWATCH_COBROS_TEMPLATE_DOC_ID`
- `OWLSWATCH_COBROS_PROFILES_PATH`
- `EMAIL_AGENT_API_TOKEN_FILE`

Optional:

- `OWLSWATCH_COBROS_NOTIFY_CHAT_ID`
- `OWLSWATCH_COBROS_NOTIFY_THREAD_ID`

Google Doc/PDF creation should use Workspace domain-wide delegation so files are owned by `info@owlswatch.com`, not by the service account. Required scopes for the configured service-account client ID:

- `https://www.googleapis.com/auth/drive`
- `https://www.googleapis.com/auth/documents`

Gmail draft creation also requires Workspace domain-wide delegation scope:

`https://www.googleapis.com/auth/gmail.compose`

No tool sends final email.
