# Security Boundaries

Owl's Watch agents are clerks. Operations, Luna, Gmail, Google Drive, and Telegram remain the systems of record or transport.

## Agent Authority

- `main`: conductor only. No business side-effect tools.
- `cuenta`: creates expense drafts only.
- `cotiza`: creates quote drafts and Drive quote sheets only.
- `correo`: creates local review records and Gmail drafts only. Gmail is the human review interface. It does not send final emails.
- `cobros`: creates cuenta de cobro Drive Doc/PDF packets, Gmail drafts with attached PDFs, and Email Desk review tasks only. It does not send final emails.
- `hotel`: reads PMS reservation operations data, creates reservations only through the guarded PMS prepare/confirm flow, and sends staff-only Telegram notifications. It does not modify or cancel reservations or send guest messages.
- `finca`: creates and updates only the Operations finca-task subsystem, attaches task photos, and sends the daily worker task report from its own profile and bot.
- `registro`: fetches guest ID images from ChakraHQ, extracts identity fields locally, writes registration data to PMS registro tools, submits SIRE reports through the browser on the SIRE domain only after recon, asks Luna for narrow guest fixes, and sends staff-only Telegram notifications. It never messages guests directly, never touches reservations, never reads the PMS database directly, and never handles SIRE credentials.
- `nomina`: prepares payroll drafts and permanent-change previews in its private Telegram profile. A local transactional service owns the v1 ledger; authenticated native human commands alone finalize, record payments and apply permanent changes. It does not send money or write Operations.

## Main

Allowed:

- Explain the Owl's Watch agent setup.
- Route people to the right Telegram topic or specialist.
- Answer lightweight operational questions about which agent handles what.

Forbidden:

- Create, approve, modify, reject, or delete expenses.
- Create quote drafts, revise quote sheets, or change pricing.
- Create, update, or send email drafts.
- Access Operations, Luna, Gmail, Drive, or Telegram side-effect tools.
- Use shell, browser, filesystem, gateway, cron, node, or web tools.
- Claim to have changed agent code/config/routing. System changes belong in this repo and are deployed by Codex.

## Cuenta

The trusted intake plugin reads only its fixed, versioned `intake-receipt/SKILL.md`
at registration and injects it for `agentId: cuenta` via `before_prompt_build`.
No model-facing filesystem permission is added; user input cannot select the file.

Allowed:

- Download/spool Telegram receipt photos.
- Upload attachments to Operations expense intake.
- Extract receipt fields using the configured vision provider.
- Create expense drafts.
- Send Telegram status replies.

Forbidden:

- Approve, modify, reject, or delete expenses.
- Access Operations endpoints outside expense intake.
- Expose tokens, receipts, or raw OCR output unnecessarily.

## Cotiza

Allowed:

- Search/read Owl's Watch Gmail for quote requests.
- Normalize and prepare quote drafts.
- Create Operations quote drafts.
- Create Google Drive quote sheets.
- Create revised draft/sheet versions of existing drafts.

Forbidden:

- Send final emails.
- Promise availability.
- Create bookings.
- Mark quotes `SENT`, `ACCEPTED`, or final.
- Use historical quote prices as pricing authority.
- Edit Drive alone as the hidden source of truth.

## Correo

Allowed:

- Search/read the configured Owl's Watch Gmail account.
- Fetch guest-shareable Luna context through `get_email_response_context`.
- Persist local review records with an owner (or explicit `unassigned`) and next-action date; reconcile closure only against Gmail SENT replies.
- Acknowledge an exact changed-message candidate with `owlswatch_email_acknowledge_item` only after a persisted review result or explicit ignored classification. This narrow tool advances scan state; it cannot send email.
- Create Gmail drafts only when compose scope and config explicitly enable it.
- Send short Telegram notifications.

Correo's Gmail history cursor and durable acknowledgements bound each scan to
changed messages. A process exit or model claim cannot acknowledge a message.
Draft writes are journaled before the provider call and reconciled by operation
identity after an uncertain outcome; the tool never blindly recreates a draft.
Recipients derive from the stored source; Telegram routes come from configuration.

Forbidden:

- Send final email.
- Delete, archive, label, or mark Gmail messages read/unread.
- Promise availability or confirm reservations.
- Invent prices, policies, access details, payment details, or booking rules.
- Use Luna broad prompt snapshots or direct database reads.
- Manage marketing campaigns or outreach sequences.

## Cobros

Allowed:

- Search/read the configured Owl's Watch Gmail account for cuenta de cobro, factura, and accounting-document requests.
- Prepare and validate cuenta de cobro fields.
- Render the configured variable Google Doc template, export a PDF, and store both in the configured Drive folder.
- Create Gmail drafts with the generated PDF attached.
- Submit Operations Email Desk review tasks.
- Send short Telegram notifications.

Forbidden:

- Send final email.
- Invent legal names, NITs, amounts, service dates, concepts, payees, or bank details.
- Reissue existing cuentas de cobro silently.
- Generate documents for amount mismatch/correction disputes without human review.
- Use quote prices or historical examples as accounting truth.
- Access unrelated Operations modules or direct databases.

Cobros stores immutable preparations and source references. Document and draft
tools accept only server-issued `preparedId` values, not model-provided banking
fields, packet paths, or approval flags. Corrections require human review;
`human_override` cannot authorize them. Concurrent writes use local locks and a
durable stage journal; unknown provider outcomes require reconciliation before
continuing. Searches and reads have per-run limits. Keep `spool/cobros/state`
across releases. Gmail recipients derive from stored source messages or the
explicit manual-source recipient setting, and Telegram destinations are fixed.

## Hotel

Allowed:

- Read PMS dashboard, lifecycle, arrival, and reservation context through the PMS tool runtime.
- Summarize arrivals, in-house guests, open checklist items, balances, and operational notes for staff.
- Send short staff-only Telegram notifications.
- Append local memory log lines for scheduled notification history.

Forbidden:

- Send guest messages.
- Modify, cancel, or delete reservations. New reservations may be created only through the PMS-signed prepare/confirm flow.
- Toggle checklist items.
- Confirm availability.
- Access PMS direct database credentials.
- Use broad PMS write, finance, admin, or restricted tools.

Reservation approval is an authenticated native Telegram command,
`/confirmar_reserva <reference>`, bound to the preparing sender, conversation,
topic, session, exact draft payload and expiry. A model's `sí` or confirmation
argument never authorizes a booking. Missing host authorization context falls
back to human review in PMS. Claims are single-use and durably journaled.

Government submissions journal each external step and receipt before advancing.
Unknown outcomes block resubmission; a known receipt with a failed PMS save
retries only that save. Preserve `state/government-submissions` and reservation
approval journals. One host and one active supervisor per profile are required
for these local locks. All staff notification destinations are configuration-bound.

## Finca

Allowed:

- List and read Operations finca tasks and safe worker display identities.
- Create, assign, reprioritize, start, progress, block, complete, cancel, and explicitly reopen finca tasks.
- Durably spool and upload task progress/completion photos.
- Send the deterministic daily outstanding-task report to the private OW Finca group.

Forbidden:

- Access payroll, salary, employee identity/banking fields, expenses, quotes, reservations, email, or other Operations modules.
- Create due dates or hard-delete task history.
- Use group membership alone as authorization; every sender must be numerically allowlisted.
- Run production with mock task storage enabled.
- Use shell, browser, filesystem, gateway, cron, node, canvas, web, or arbitrary messaging tools.

## Registro

Allowed:

- Read and write PMS registration rows only through `registro` classification tools.
- Record local extraction results, guarded status transitions, validation exceptions, and submission attempts.
- Ask Luna to send one narrow guest correction request inside the WhatsApp service window.
- Send short staff-only Telegram notifications for exceptions and sweep summaries.
- Use a local or tailnet-only vision extractor and deterministic MRZ checksum parsing.
- Use the browser only for the SIRE portal domain `apps.migracioncolombia.gov.co`, and only after the recon-gated browser routine lands.

Forbidden:

- Direct PMS or Luna database access.
- Guest messaging outside Luna.
- Browser navigation outside `apps.migracioncolombia.gov.co`.
- Live SIRE browser automation before the recon-gated routine is implemented.
- Invent identity fields, city codes, motives, occupations, or receipt references.
- Send document images, raw IDs, full government payloads, or tokens to Telegram.
- Access PMS tools outside the `registro` classification.

## Nómina

The dedicated `nomina` profile has its own private manager bot/route, workspace,
agent directory and state. Each read and write verifies trusted host Telegram
sender/account/chat/topic/session metadata against runtime-only allowlists.
Group membership alone is insufficient. Its fixed payroll skill is injected by
the native plugin without granting filesystem access.

Allowed model tools:

- `nomina_status`, `nomina_list_payees`, `nomina_get_loans`: current scoped state.
- `nomina_prepare_change`: preview a permanent profile, loan, installment or
  loan-event change without applying it.
- `nomina_prepare_run`, `nomina_adjust_draft`: prepare revisioned payroll drafts
  and explicit one-period adjustments.
- `nomina_get_run`, `nomina_history`: read current and historical persisted runs.
- `nomina_prepare_finalize`, `nomina_prepare_paid`: prepare exact finalization
  or reported-payment transitions, never execute them through a model tool.
- `nomina_prepare_supersede`, `nomina_prepare_reverse_payment`: prepare audited
  corrections that preserve the original snapshot and ledger history.
- `nomina_export`, `nomina_archive_status`: write restricted snapshot reports and
  inspect/retry configuration-bound archive delivery independently of payroll.

The trusted native `/revisar_nomina TOKEN` command renders the exact saved action
without recording review yet. The host verifies its hash and requires a complete
durable Telegram send with valid platform receipts before the internal
`review-delivered` call may revalidate the actor, version and hash and record
review. Only then is a separate **Confirmar** button offered. A **Revisar** button
may be derived from a server-issued review command in the final reply; plain
agreement or model-written confirmation text cannot create approval authority.
Partial, failed, suppressed or receipt-less review delivery does not pass the
gate. Delivery evidence does not prove that a human read the text.
Only the subsequent `/confirmar_nomina TOKEN` applies permanent changes.
Both user commands require authenticated host authorization, the same
preparing sender and route/session, a live token and unchanged prepared state.
Confirmation is single-use and requires that exact native review.
User text, model-supplied approval fields and forwarded commands cannot approve.
Finalization reserves loans; human acknowledgment of completed payments posts
repayments. No bank payment or reversal is executed. SQL transactions, revisions,
stable request IDs and unique business identities enforce retry boundaries.
The SDK tool context currently does not expose source Telegram message IDs;
tool-call audit IDs must not be described as complete source-message deduplication.

Amounts, rounding, allocations and approved payment destinations are resolved by
code. Normal model-tool replies mask banking details. Full payment destinations
appear only in the authenticated native review, restricted local reports and
explicitly configured private Drive files.

`nomina_export` can return exact `/informe_nomina RUN_ID csv` and
`/informe_nomina RUN_ID html` download commands, shown as native download buttons.
The native command requires host authorization and local allowlist checks,
exports a persisted non-draft run and sends only the server-selected, contained
CSV/HTML document to that authorized route. It accepts neither arbitrary paths
nor alternate recipients. Backup/key/config files and JSON are not native
download options. Private document delivery works without Drive; an export or
local path alone is not proof of a sent attachment. These native sends do not
grant the model an arbitrary messaging tool or any payment authority.

Archive upload IDs are journaled before provider creation; unknown outcomes retry
the same identity. The required private archive boundary pins
`archive.drive_folder_id`, a dedicated credential file in
`archive.google_credentials_file`, and explicit `archive.allowed_reader_emails`.
Every direct/inherited folder permission must be an identifiable individual user
on that allowlist; groups, domains, public/anyone, unlisted users, missing
allowlists and incomplete inspection fail closed. These are access checks, not
permission-changing tools. Credentials may be `service_account` or dedicated
`authorized_user` OAuth. Personal Drive requires the dedicated OAuth app and a
folder created/authorized for it under `drive.file`; a service account uses an
appropriate Shared Drive. No other agent's credentials, consent or token files
may be shared, and inaccessible folders never justify broadening scopes. Verify
this boundary in the integrated release before any live activation.
Encrypted backups use SQLite's online-backup API and exclude
keys/configuration. Upload workers serialize with a process lock and each attempt
is bounded to one backup plus at most three reports. Status separates local and
uploaded versions, exposes the last completed upload time/age and at most 30
already-uploaded report links, never backup links or new public permissions.
Restore is offline/operator-only into a new private directory after bundle,
manifest, database/version and foreign-key validation. Private off-machine setup
remains pending the user's destination/access/key-custody choices; synthetic
restore and fake-provider tests are not evidence of active backup or live recovery.

The private delivery journal hashes inbound identity and reply text, but retains
route, sender, session/run identifiers, timestamps and statuses. It stores no
financial message bodies or raw provider errors; hashes do not make the retained
metadata anonymous or encrypted. Inbound entries require trusted host message
and session IDs. Outbound hooks lack source/run IDs, so text equality is not proof.
The source-bound final hook may take sole delivery ownership via the public SDK
durable sender, record receipts against that exact inbound ID, and suppress a
second core send even after uncertainty. No arbitrary recipient/model messaging
tool is added. Missing host metadata falls back to core delivery without a receipt
claim. Native confirmation requests are journaled before execution and their
results are sent durably. The review gate verifies normalized effective text as
well as delivery receipts; altered text cannot authorize a change.
Generation success is not proof of delivery. Journal observations do not replace
the native review gate or deduplicate financial execution. There is no automatic
journal-row retention limit and no automatic replay of failed/uncertain requests.

An operator-owned, separately enabled maintenance LaunchAgent may run independent
archive retries, read-only health probes and fixed operational notices. This is
not a model scheduling grant. Its workspace enable file and isolated profile
checks gate execution; a process lock and private maintenance SQLite state bound
overlap, backoff and notice duplication. It attempts at most two archive jobs and
one notice per run, warns about unverified deliveries after 10 minutes (grouped
at most hourly) and channel failures after 15 minutes. Notice delivery is limited
to one pinned route on the default account with matching allowlists, no financial
payload or arbitrary recipient. Unknown sends are not resent; explicit 429s get
at most three total attempts, at least an hour apart. The channel may be unable
to carry its own outage notice. Failed delivery never repeats a payroll mutation.

Maintenance never restarts the gateway. OpenClaw and the dedicated gateway's
launchd `RunAtLoad`/`KeepAlive` settings own liveness. The installer preserves that
gateway's environment and private stderr, and requires a separate gateway reload.
Both services are GUI-user LaunchAgents: reboot recovery requires login, not a
pre-login daemon. Scoped log rotation does not constitute lossless audit storage
or delivery-journal pruning. These source capabilities are not deployment claims.

Forbidden: model finalization/payment authority, bank transfers, employee or
arbitrary Telegram messages, public archive sharing, tax-rule inference,
Operations writes, arbitrary paths, secrets in prompts, broad shell/file/web/
browser tools and model-owned or automatic financial schedules. Configuration
starts disabled with
unusable numeric placeholders and no production data. Runtime state is preserved
across isolated source deployments. The shared runtime guard is loaded for this
profile only; deployment does not modify other agents or watchdogs.

## Tool Policy

Each agent uses `tools.profile: "minimal"` plus explicit `alsoAllow` entries for narrow `owlswatch_*` tools.

Broad tools stay denied by default:

- `exec`
- `browser` except Registro's documented SIRE-domain-only browser policy
- `gateway`
- `cron`
- `nodes`
- `canvas`
- `group:fs`
- `group:web`
- `bundle-mcp`

Any change that broadens tools must include:

- the reason
- the exact agent
- the exact tool names
- a smoke test
- a review of whether a narrower `owlswatch_*` tool would be safer

## Secrets

Never commit tokens, service-account JSON, auth profiles, runtime sessions, memory logs, receipt spools, raw Gmail content, or generated quote sheets.

## Runtime control

The native `owlswatch-runtime-guard` plugin exposes no model tools. It applies
trusted run limits, records content-free usage/outcomes, and pauses requests to
a provider for 30 minutes after an observed insufficient-credit failure. It does
not hold provider credentials or infer approval. Paid idle heartbeats are
disabled; deterministic schedule preflights decide whether a model is needed.
Its audited local hooks require `hooks.allowConversationAccess: true` to receive
run boundaries, aggregate usage, and terminal provider-error metadata. The hook
inspects terminal assistant status locally and never persists conversation text.
Duplicate legacy MCP registrations are disabled only when the corresponding
native plugin is enabled; their environment configuration is preserved.
