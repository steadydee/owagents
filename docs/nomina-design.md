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
payment. The human first opens `/revisar_nomina TOKEN`, which renders the exact
saved action and records a native review. That review supplies
`/confirmar_nomina TOKEN`; confirmation fails without the matching review. Both
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

## Durability, exports and future Operations integration

SQLite transactions, unique business identities, revisions and stable request IDs
protect retries. Pending tokens are one-use and expire. The installed OpenClaw
tool context does not expose a trustworthy Telegram message ID, so the system does
not claim complete Telegram-redelivery deduplication. Preserve the request ID for
the same intended edit/retry and consult current draft/history after uncertainty.
Host tool-call IDs are audit references, not Telegram message identities.

The workspace and state directories are private; configuration, database and
reports are restricted to the runtime owner. Sensitive production data never
enters git. Finalized JSON snapshots, CSV payment lists and printable HTML reports
are published atomically and retained under content hashes. Normal chat responses
mask destinations; the authenticated native review and private reports retain
the complete payment destination so the manager can verify it exactly.

Optional private Drive archiving uses one configured folder and locally held
service-account credentials. Reports remain private files; database recovery
copies are encrypted with a separate Fernet key. The SQLite online-backup API
captures committed WAL state consistently. Keys and runtime configuration are
excluded and must be retained separately. An outbox persists provider file IDs
before upload, allowing reconciliation of an uncertain create without replaying
payroll actions. Native confirmation queues archive work without a network call;
the next user workflow performs bounded archive retries (at most two) and reports
whether the off-machine copy is current. No background schedule is installed.
Archive failure and business status are independent.

Model history responses are bounded; loan previews currently show the latest
50 events and flag truncation. This bounds conversation exposure without deleting
older ledger data. Full retained state is present in consistent recovery backups.

Versioned JSON exports identify `owlswatch-nomina` authority, original IDs and
snapshot-only import semantics. A future Operations importer must preserve those
identities and history, reconcile balances without reapplying repayments, then
switch authority once. v1 does not dual-write or change Operations code.
