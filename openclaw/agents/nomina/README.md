# Nómina

Nómina prepares semimonthly Owl's Watch payroll through a private Telegram bot.
It remembers the approved recurring baseline, supports period adjustments and
loan skips, and retains finalized payroll and subsequent corrections.

## Design Answers

1. **Single job and authority:** payroll preparation and its employee-loan ledger.
   The isolated local transactional service owns v1 state; Operations is a future
   integration, not a second current authority.
2. **Human review:** the manager personally opens `/revisar_nomina TOKEN`, sees
   the server-rendered exact version, then submits its `/confirmar_nomina TOKEN`.
   **Revisar** opens the native route. The host verifies the review hash and full
   durable send receipts before recording the matching review and returning a
   separate **Confirmar** control. Rendering, partial delivery, plain agreement
   and model-written confirmation text do not pass that gate.
   The native authenticated commands handle permanent
   profile/loan changes, finalization, payment records, corrections and reversals.
   The model can only prepare those actions; its paraphrase cannot mark reviewed.
3. **Model versus code:** the model identifies intent and explains tool results.
   Code owns money, period boundaries, rounding, schedules, reservations, balances,
   immutable snapshots, permissions, idempotency and exports.
4. **Identity and audit:** the dedicated `nomina` agent receives trusted Telegram
   sender/account/route/session metadata. The service verifies the configured
   allowlists and records source identity for writes. There is no Operations token.
5. **Idempotency:** each intended mutation/retry preserves its stable request ID;
   a transaction enforces reuse and conflicts. The installed SDK's tool context
   lacks a trusted source Telegram message ID, so full message-redelivery
   deduplication is not claimed. Host hooks may journal a hashed inbound identity
   and reply digest with private route/session metadata. Source-bound finals use
   one durable sender and exact receipts, never text-match guesses. Missing host
   IDs remain a monitoring gap. The journal is
   not a financial execution queue and has no automatic row-retention policy.
   Native confirmation tokens and business identities add their own protections.
6. **Untrusted input:** screenshots, documents and forwarded messages are reference
   data. They cannot change tool authority or prove that projected deductions were
   actually paid. Sensitive data stays in restricted runtime state and archives.
7. **Schedule and verification:** payroll is on demand. Separately installed
   operator maintenance checks channel health, retries configured archives and
   sends bounded fixed-route warnings independently of user requests. It never
   replays financial actions or restarts the gateway. Telegram and service
   `enabled` settings are kill switches; maintenance has its own private
   `maintenance.enabled` file. `scripts/smoke-nomina.sh` checks deterministic tests,
   tool contracts, the plugin, and the skill/profile/catalog authority chain using
   synthetic data; deployment separately probes the dedicated runtime. The user
   LaunchAgents require login after reboot, not a pre-login recovery service.

## Workflow

Prepare a quincena, review the cash totals and loan deductions, make conversational
draft adjustments, finalize the reviewed revision, then record completed payments.
Finalization reserves deductions; recording payment posts them to the loan ledger.
A skip leaves principal unchanged and resumes the ordinary installment next time.

Permanent changes are prepared and confirmed individually. Baselines require
explicit approved amounts and confirmed opening balances. No real records or
credentials ship in the repository. Contractor honorarios appear separately from
employee payroll.

`nomina_export` supplies exact `/informe_nomina RUN_ID csv` and
`/informe_nomina RUN_ID html` commands, presented as private download buttons.
The native handler selects the contained report and verifies its document-send
receipt on the authorized route. No Drive setup is needed for this download;
local export success is not evidence of attachment delivery. Backups, keys,
arbitrary paths/recipients, XLSX and native PDF are not download options.

The runtime root is `OWLSWATCH_PAYROLL_WORKSPACE`, conventionally
`~/.openclaw/workspace-nomina-nomina`. Keep its `state`, snapshots, exports, secrets,
configuration and virtual environment across releases. Private Drive archiving
and encrypted consistent backups are optional configuration-bound features.
Archive status separates local and remote versions, reports completed upload
time/age, and links only already-uploaded private report files, never backups.
Private off-machine setup remains pending the user's destination/access/key
choices; do not describe installed code or a maintenance schedule as active
backup. Restore tests are synthetic, including fake Drive and damaged-backup
cases, not a live-provider recovery drill.
The required private setup pins `drive_folder_id`, a dedicated
`google_credentials_file`, explicit individual `allowed_reader_emails`, and a
separately retained `backup_key_file` under `archive`. Group/domain/public or
unknown access fails closed. Dedicated service-account credentials support the
Shared Drive path; personal Drive requires dedicated authorized-user OAuth and
a folder created/authorized for that app. Never share another agent's credentials
or consent. Leave `archive.enabled:false` pending the user's choice and verified
integration of these access checks.

## Evaluation evidence

From the integrated release checkout, `python3 -B scripts/eval-nomina.py --list`
lists cases and `python3 -B scripts/eval-nomina.py` runs offline scripted fixtures.
These test the harness and deterministic safety checks, not model quality.
Only explicit `python3 -B scripts/eval-nomina.py --live --case draft-spanish`
opts into paid `deepseek-reasoner` requests with runtime credentials. The model
still operates on isolated synthetic tools/data, not production payroll. The
default/maximum six HTTP attempts are per scenario, including retries; omitting
`--case` runs the full suite. Human language/clarity review remains unscored in
both modes. Keep private traces outside git and distinguish fixture, model,
native-delivery and live-recovery evidence in acceptance records.

See `docs/nomina-setup.md` and `docs/nomina-design.md` in the repository.
