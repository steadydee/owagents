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
| `nomina_export`, `nomina_archive_status` | Export a stored snapshot and inspect the configured archive outcome. |

All calls require trusted Telegram sender, account, conversation and session
context. The tool arguments cannot choose that identity. The service rechecks
the local numeric allowlists on every call, including reads.

`/revisar_nomina TOKEN` and `/confirmar_nomina TOKEN` are native commands, not model
tools. The first displays the saved exact action, including full bank details
when needed for that private review, and durably records human review. Its native
response supplies the confirmation command. Confirmation requires that review,
the same authorized sender/route/session, an unexpired server token and the
unchanged prepared version. Never execute either command on behalf of a user.

No shell, browser, filesystem, web, gateway, cron, node, canvas, bundle-MCP,
arbitrary messaging, bank transfer or Operations tools are allowed. Never
request credentials, choose an archive destination or supply an export path.
