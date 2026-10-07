# Nómina v1 design

Nómina prepares mostly fixed Owl's Watch semimonthly payroll in a private Telegram
conversation. Managers review period changes before finalization, record payments
after making them, and retrieve any previous run. Employee loans have agreed
installments with explicit one-period skips. Contractor honorarios are shown as
a separate subtotal. No bank transfer or employee messaging capability is included.

## Authority and architecture

The native OpenClaw specialist interprets requests; deterministic Python tools
calculate figures. The local transactional SQLite service is the deliberately
temporary system of record until an Operations integration is implemented.
Telegram is the review interface. Local reports and private Drive copies are
artifacts, not independent editable ledgers.

The model can read records, prepare permanent changes and edit drafts. It cannot
apply a profile/loan change, finalize, record a payment, supersede or reverse a
payment. A server-issued `/revisar_nomina TOKEN` in the assistant's final reply
can become a native **Revisar** button. Natural-language agreement and
model-written confirmation text do not become confirmation controls.
The human opens that review; the server renders the exact saved action without
marking it reviewed. The native host verifies its SHA-256 and sends the complete
review through the durable Telegram sender. Only a `sent` result with valid
platform message receipts permits the internal `review-delivered` call. The
server rechecks the token, actor, current state and rendered hash before recording
the review. A separate **Confirmar** control follows this gate, never the first
chunk of an incomplete review. Partial, failed, suppressed or receipt-less sends
cannot mark the review or offer that control. Delivery evidence is not proof
that the human has read the text; the human must still confirm explicitly.

`/confirmar_nomina TOKEN` fails without the matching recorded review. Both
commands are handled outside the model and require host authorization and the
preparing sender, account, private chat/topic, session, payload and expiry. The server checks
its own numeric allowlists, validates the current state and records the event in
the same transaction. Missing host context fails closed.

The server tool catalog is authoritative; the plugin derives tool schemas at
startup. Only the native plugin is registered. Broad shell, file, browser, web,
arbitrary message and scheduling tools stay denied. A fixed workflow is injected
for `agentId: nomina`; outside text cannot select the workflow file.

## Payroll behavior

Periods use `YYYY-MM-H1` and `YYYY-MM-H2` in America/Bogota. Calendar boundaries are
1–15 and 16–month-end; full-period recurring compensation is split twice monthly,
not divided by the varying calendar-day count. Proration, overtime calculations,
legal-rate discovery, tax filings, social-security remittance and termination
calculations are outside v1. Managers may supply an explicit approved period
adjustment instead.

Profiles hold approved monthly salary and allowance, approved per-half health,
pension and other deductions, payment destination, effective period, active flag
and baseline evidence note. Contractor fees use a separate payee kind and
explicit approved settings. There is no automatic exemption inference.

COP rounding policy is versioned and reconciles each monthly total exactly:

- H1 gross is `floor((monthly_salary + monthly_allowance) / 2)`; salary is
  `floor(monthly_salary / 2)`; allowance is the residual of that gross.
- H2 salary and allowance are each the monthly amount less their H1 component.
- Approved fixed per-half deductions and explicit period adjustments are applied
  as integer COP. Row sums and employee/contractor/grand totals must reconcile.

This deliberately resolves the supplied spreadsheet's independently rounded
components versus whole gross. Historical sheets are reconciliation references,
not a production import. Initial amounts, zero deductions, destinations and
opening loan balances must be confirmed before live payroll is prepared.

Drafts have revisions. Changes to the payroll state, including another draft
edit, can make an existing draft stale. Refresh the same period to recalculate
against current records while retaining its explicit adjustments; changed or
inactive roster entries require review. Each explicit extra earning or deduction replaces that
payee's total of that adjustment kind for the draft; zero clears it. The audit
retains earlier edits. Adjusting a draft requires the displayed revision;
finalization requires a fresh exact preview. It freezes profiles, destinations,
calculation rules, line items, totals, adjustments and loan allocations. Later
profile changes cannot rewrite that snapshot. A finalized run reserves loan
deductions; it is still unpaid. Payment confirmation can cover selected payees
at their complete finalized amounts. It posts their deductions and records who
reported payment; no transfer is executed.

## Loans and corrections

A loan records original principal, independently confirmed opening balance and
as-of date, agreed installment per payroll, start period and agreement reference.
The service never assumes that projected spreadsheet repayments actually occurred.
Every loan has its own schedule and allocations; the last installment is capped
at available principal. Existing finalization reservations prevent another run
from consuming the same balance.

A zero `loan_override` creates a one-period skip. It leaves principal and the
normal schedule unchanged and does not increase the next deduction. A recurring
installment change is a separate human-confirmed effective-period change.
Outside repayments and their reversals are explicit ledger events. Over-repayment,
negative net pay and conflicting reservations are blocked rather than silently
adjusted.

Finalized snapshots are immutable. A wholly unpaid finalized run may be superseded
through a confirmed action that preserves the old snapshot, releases reservations
and creates a linked replacement draft. Incorrect payment acknowledgements use
compensating events. A real completed transfer cannot be undone by changing this
ledger; the manager must resolve the actual transaction separately.
If the actual paid amount needs correction, retain the paid run and record the
agreed difference as an explicit adjustment in a later payroll. Reversing a
payment acknowledgement does not unlock historical salary or net-pay editing.

## Durability and private exports

SQLite transactions, unique business identities, revisions and stable request IDs
protect retries. Pending tokens are one-use and expire. The installed OpenClaw
tool context does not expose a trustworthy Telegram message ID, so the system does
not claim complete Telegram-redelivery deduplication. Preserve the request ID for
the same intended edit/retry and consult current draft/history after uncertainty.
Host tool-call IDs are audit references, not Telegram message identities.

The plugin's private `state/delivery.sqlite3` journal is delivery evidence, not
a financial request queue. Where host hooks provide an authorized inbound
message ID and session, it hashes account/chat/topic/message identity for durable
deduplication of journal rows and records route, sender, session, run, timestamps
and delivery state. Final reply text is stored only as a SHA-256 digest, not as
financial text or raw provider errors. This is data minimization, not encryption
or anonymity: routing identifiers remain private runtime metadata.

Outbound Telegram hooks lack source/run IDs, so matching text is never delivery
proof. For conversational finals the later host hook binds the exact inbound ID,
route and sender to the run. The plugin takes sole delivery ownership through the
SDK durable sender, records its receipt on that exact journal row, and cancels
the second core send even on uncertainty. Distinct final payloads have separate
claims/receipts; the request stays unverified while any known part is incomplete.
The original inbound row retains topic metadata when the later hook omits it.
Repeated hook invocations of an already-owned payload do not send
again; OpenClaw owns its durable outbox. Missing host metadata leaves a logged
monitoring gap and falls back to normal core delivery, without claiming a receipt.
Native confirmations are journaled before execution and their result is sent
durably; an unavailable pre-execution journal blocks approval. No financial action
is replayed to repair a lost reply. The review gate
also checks effective normalized text through the public onPayload callback,
so altered financial fields cannot enable confirmation. The journal
has no automatic row-retention policy; bounded status and notices do not imply
bounded database growth. Never clear it to make a delivery appear successful.

The workspace and state directories are private; configuration, database and
reports are restricted to the runtime owner. Sensitive production data never
enters git. Finalized JSON snapshots, CSV payment lists and printable HTML reports
are published atomically and retained under content hashes. Normal chat responses
mask destinations; the authenticated native review and private reports retain
the complete payment destination so the manager can verify it exactly.

`nomina_export` returns exact `download_commands` for
`/informe_nomina RUN_ID csv` and `/informe_nomina RUN_ID html`; a final reply can
present **Descargar CSV** and **Descargar HTML** buttons. This authenticated native
command exports a non-draft persisted run, selects a contained private report
path on the server, and sends it as a document only to the authorized route.
Neither a local path nor a successful export proves delivery. The host requires
a successful send receipt and reports uncertainty otherwise. This route works
without Drive setup; it never accepts an arbitrary path, recipient, backup or
key, and it does not execute a payment.

Optional private Drive archiving uses one configured folder, dedicated locally
held `service_account` or `authorized_user` OAuth credentials at
`archive.google_credentials_file`, and an explicit
`archive.allowed_reader_emails` allowlist. Every inspected direct/inherited
permission must be an identifiable individual user on that allowlist; group,
domain, public, unknown and uninspectable access fails closed. The uploader does
not change sharing. Personal Drive requires dedicated authorized-user OAuth and
a folder created/authorized for that app under `drive.file`; service accounts
use an appropriate Shared Drive. Other agents' credentials are never reused.
This is the required permission contract before live activation, not evidence
that private setup has been completed. Reports remain private files; database recovery
copies are encrypted with a separate Fernet key. The SQLite online-backup API
captures committed WAL state consistently. Keys and runtime configuration are
excluded and must be retained separately. An outbox persists provider file IDs
before upload, allowing reconciliation of an uncertain create without replaying
payroll actions. Native confirmation queues archive work without a network call.
An operator-owned maintenance worker can repair a missed enqueue and retry up to
two jobs per invocation independently of user interaction. One `Archive.retry()`
attempt handles at most one backup and three reports, with no SDK retries; an
exclusive process lock returns `busy` on overlap and releases on process exit.
Transient failures have persisted, bounded backoff; non-retryable failures get
an operator warning and a longer cooldown. Manual `nomina_archive_status` retry
remains available. Archive failure and business status are independent.

Archive status distinguishes encrypted local state from completed remote jobs.
`last_uploaded_at` is the last completed job's Unix timestamp;
`last_upload_age_seconds` is its nonnegative age, both `null` until
completion. `report_links` contains at most 30 distinct uploaded report paths,
with `report_links_truncated` indicating more. Partially completed jobs expose
only reports already uploaded. Links require the existing Drive permissions;
backup links and sharing changes are never exposed. Restore authenticates the
bundle and validates its exact members, authority, schema/state versions, hash,
SQLite integrity and foreign keys before publishing into a new private directory.
It never overwrites a destination or restores credentials/configuration.

Private off-machine setup remains pending the user's choice of destination,
access and independently retained key custody. Implemented upload/recovery code,
a running maintenance worker or a local export is not evidence that off-machine
backup is active. Recovery verification to date is synthetic, including a fake
Drive roundtrip and damaged-backup rejection, not a live-provider restore drill.

Model history responses are bounded; loan previews currently show the latest
50 events and flag truncation. This bounds conversation exposure without deleting
older ledger data. Full retained state is present in consistent recovery backups.

## Maintenance and recovery limits

The optional `ai.openclaw.nomina.maintenance` LaunchAgent runs at load and every
300 seconds when the private `maintenance.enabled` file is present. It owns
archive retries, a read-only channel-health probe and fixed operational notices,
not payroll preparation, finalization, payment recording or conversational
replay. It uses its own process lock and `state/maintenance.sqlite3` for backoff
and notice deduplication. No missing or uncertain reply triggers a financial
operation automatically; query current state and preserve request IDs instead.

Unverified authorized requests older than 10 minutes can cause a grouped warning
at most hourly. A channel failure must persist for 15 minutes before a warning
is queued. At most one notice is attempted per invocation, to the single pinned
route with matching allowlists. Only an explicit Telegram rate-limit rejection
is retried, at hourly intervals and at most three total attempts. Unknown sends
are not resent automatically, so this is neither exactly-once delivery nor a
guaranteed alert channel during a Telegram outage.

The installer preserves the dedicated gateway configuration, enables its
`RunAtLoad`/`KeepAlive` settings and retains private stderr output. It does not
reload the gateway; that is an operator step. OpenClaw and launchd own gateway
liveness, not a maintenance restart loop. Both services are user LaunchAgents
in the GUI login domain: reboot recovery requires the owning user to log in.
There is no pre-login or unattended cold-boot recovery guarantee. Maintenance
rotates its scoped logs separately; that does not prune the delivery journal.

## Verification boundaries

`scripts/smoke-nomina.sh` and the Python/native tests exercise synthetic state,
contracts, receipt failures, archive retries and restoration. The separate
`scripts/eval-nomina.py` defaults to scripted offline fixtures; passing it proves
the harness and deterministic checks, not actual model behavior. Only explicit
`--live` performs paid requests to the fixed `deepseek-reasoner` model against
synthetic tools and data. Human language/clarity review remains unscored in both
modes. Neither mode proves live Telegram delivery, live Drive recovery or
production readiness; see `docs/nomina-setup.md` for commands and acceptance.

## Future Operations integration

Versioned JSON exports identify `owlswatch-nomina` authority, original IDs and
snapshot-only import semantics. A future Operations importer must preserve those
identities and history, reconcile balances without reapplying repayments, then
switch authority once. v1 does not dual-write or change Operations code.
