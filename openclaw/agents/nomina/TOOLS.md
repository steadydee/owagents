# Nómina Tools

The Python server owns the catalog and schemas. The native plugin derives tools
from that catalog; it is the only model transport.

| Tools | Authority |
| --- | --- |
| `nomina_status`, `nomina_list_payees`, `nomina_get_loans` | Read configuration readiness and scoped current records. |
| `nomina_prepare_change` | Prepare a profile, loan, installment or loan-event change for native human confirmation. |
| `nomina_prepare_run`, `nomina_adjust_draft` | Create and adjust revisioned drafts. |
| `nomina_get_run`, `nomina_history` | Read persisted run snapshots and history. |
| `nomina_prepare_finalize`, `nomina_prepare_paid` | Prepare finalization or a recorded payment for native human confirmation. |
| `nomina_prepare_supersede`, `nomina_prepare_reverse_payment` | Prepare a linked correction or reversal for native human confirmation. |
| `nomina_export` | Export a non-draft stored run and return exact private native `download_commands` for CSV and HTML. Export is not attachment delivery. |
| `nomina_archive_status` | Inspect local/uploaded versions, last upload time/age and uploaded private report links; `retry:true` attempts bounded archive work, never payroll replay. |

All calls require trusted Telegram sender, account, conversation and session
context. The tool arguments cannot choose that identity. The service rechecks
the local numeric allowlists on every call, including reads.

`/revisar_nomina TOKEN` and `/confirmar_nomina TOKEN` are native commands, not model
tools. The first renders the saved exact action, including full bank details
when needed for that private review, without marking it reviewed. The host must
verify the rendered hash and complete durable Telegram delivery with valid
platform receipts. Its internal `review-delivered` call rechecks the current
state and hash before recording the review. Only then does a separate native
**Confirmar** button follow. Partial, failed or unverified delivery cannot pass
this gate. A **Revisar** button can be generated from the server-issued review
command; plain agreement or model-written confirmation text is not authorization.
Confirmation requires that recorded review,
the same authorized sender/route/session, an unexpired server token and the
unchanged prepared version. Never execute either command on behalf of a user.

`/informe_nomina RUN_ID csv` and `/informe_nomina RUN_ID html` are native commands,
not model tools. Present the exact `download_commands.csv` and
`download_commands.html` from `nomina_export`; the plugin can show **Descargar CSV**
and **Descargar HTML** controls. The authenticated handler chooses a private,
contained export and sends it as a document only to the authorized route. It
accepts no path/recipient, rejects drafts, and does not offer JSON, backups or
keys. It works without Drive. Do not claim delivery until the native receipt is
verified; a local `files` path is not a user-download URL.

Archive status exposes `last_uploaded_at` (Unix seconds for the last completed
job), `last_upload_age_seconds` (nonnegative seconds), `report_links` and
`report_links_truncated`. Time/age are `null` before any job completes. At most 30
distinct uploaded reports are linked; incomplete jobs expose only uploaded report
files, never encrypted database backups. Links do not change Drive permissions.
Private setup remains pending user choice until configured and verified; neither
local encryption nor synthetic restore tests prove current off-machine protection.
Required operator configuration pins `archive.drive_folder_id`, dedicated
`archive.google_credentials_file` and explicit `archive.allowed_reader_emails`.
Only allowlisted individual users are permitted; group/domain/public or unknown
access fails closed. The credential file may be `service_account` or dedicated
`authorized_user` OAuth. Personal Drive requires the dedicated OAuth app's
created/authorized folder; no other agent's credentials or consent may be shared.
These setup fields are not model tool arguments or permission to activate backup.

Operator maintenance is outside this catalog: independently scheduled archive
retry, read-only channel health checks and fixed-route attention notices only.
Its private hashed delivery journal uses source-bound durable receipts for final
replies. Missing host IDs remain an observability gap; it is not a financial replay queue.
Never clear journal state or replay an uncertain business action. Maintenance
has a separate enable file and does not restart the gateway; its user LaunchAgent
and the gateway require login after reboot. No new scheduling/messaging tool is
granted to the model.

No shell, browser, filesystem, web, gateway, cron, node, canvas, bundle-MCP,
arbitrary messaging, bank transfer or Operations tools are allowed. Never
request credentials, choose an archive destination or supply an export path.
