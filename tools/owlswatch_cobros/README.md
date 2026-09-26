# Owl's Watch Cobros Tools

This plugin exposes narrow tools for Cobros, the Owl's Watch cuenta de cobro drafting agent.

The tools are intentionally scoped:

- Gmail search/read is read-only.
- Drive writes are limited to rendering the configured variable cuenta de cobro template, creating the exported PDF, and placing both in the configured Cobros folder.
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

The Google Doc named by `OWLSWATCH_COBROS_TEMPLATE_DOC_ID` must be a variable template. Required placeholders:

- `{{DEBTOR_LEGAL_NAME}}`
- `{{DEBTOR_NIT}}`
- `{{PAYEE_NAME}}`
- `{{PAYEE_NIT}}`
- `{{PAYEE_CEDULA}}`
- `{{AMOUNT_COP}}`
- `{{AMOUNT_WORDS_ES}}`
- `{{CONCEPT}}`
- `{{SERVICE_DATES}}`
- `{{CLIENT_REFERENCE}}`
- `{{PAYEE_BANK}}`
- `{{PAYEE_ACCOUNT_TYPE}}`
- `{{PAYEE_ACCOUNT_NUMBER}}`

Optional:

- `OWLSWATCH_COBROS_NOTIFY_CHAT_ID`
- `OWLSWATCH_COBROS_NOTIFY_THREAD_ID`

Google Doc/PDF creation should use Workspace domain-wide delegation so files are owned by `info@owlswatch.com`, not by the service account. Required scopes for the configured service-account client ID:

- `https://www.googleapis.com/auth/drive`

Gmail draft creation also requires Workspace domain-wide delegation scope:

`https://www.googleapis.com/auth/gmail.compose`

No tool sends final email.


## Integrity And Recovery Contract

`read_gmail_thread` persists the full provider source and returns `sourceId` plus a
bounded preview. `prepare({sourceId})` reads that stored source; manual requests
use `prepare({raw_text})`. Only ready results receive an opaque `preparedId`.
No `human_override` or mutable financial override is accepted. Both
`create_packet({preparedId})` and `create_gmail_draft({preparedId})` load immutable
bank, payee, amount, status and source routing from local state. Older mutable
`prepared`, `packet`, `to`, body, and local-path arguments are rejected.

State lives only in `<workspace>/spool/cobros/state/journal.sqlite3` (0600) with
per-preparation process locks. Keep this journal and spool when deploying or
restarting. It contains private source/financial data; never commit it, clear it
to retry a workflow, or run two hosts against independent copies of its state.
Repeated preparation of the same Gmail thread is idempotent; a changed financial
record for that thread requires human reconciliation. Identical manual prepared
records also deduplicate. A matching legacy document title in the configured
folder blocks creation until a human reconciles the pre-journal artifact.

Each external create is recorded as attempting before IO. Completed document,
PDF, Gmail and Operations stages are reused. Interrupted Drive creates reconcile
using `appProperties.cobrosEffectKey`; Gmail drafts use a deterministic Message-ID.
No match or multiple matches halt the workflow with `outcome_unknown` instead of
recreating anything. This includes the crash window before a provider response.
Operations intake carries a stable Idempotency-Key and actor/correlation headers;
its unknown result requires manual review because this package has no authoritative
read endpoint for that external task. The already-created Gmail draft is returned.
A human must check provider artifacts and app audit records before any journal
repair; no repair/reset authority is exposed to the agent.

The PDF attachment is loaded only from the journaled spool path and verified by
SHA-256. A retrieved Gmail source fixes the draft recipient/thread; a manual
request needs optional `OWLSWATCH_COBROS_DRAFT_TO`, otherwise the packet remains
available for manual review without a draft. Telegram always uses configured
chat/topic; mismatching destination arguments are rejected.

The plugin derives schemas from `server.py catalog`, has a bounded subprocess
lifetime, and applies a trusted-run hook limit of six Gmail searches, twelve reads,
and two identical queries. Existing grants and final-send boundaries are unchanged.
Provider reconciliation uses documented [Gmail draft search](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/list)
and [Drive application properties](https://developers.google.com/workspace/drive/api/guides/properties).

## Validation

`./scripts/smoke-cobros.sh` runs extraction/render smoke checks, fake-provider
integrity/recovery/concurrency tests, and the read-budget tests. It creates a
temporary isolated workspace and never sends Gmail, Drive or Telegram mutations.
