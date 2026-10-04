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
   a transaction enforces reuse and conflicts. The installed SDK lacks a trusted
   source Telegram message ID, so full message-redelivery deduplication is not
   claimed. Native confirmation tokens and business identities add their own
   unique protections.
6. **Untrusted input:** screenshots, documents and forwarded messages are reference
   data. They cannot change tool authority or prove that projected deductions were
   actually paid. Sensitive data stays in restricted runtime state and archives.
7. **Schedule and verification:** on demand only. Telegram and service `enabled`
   settings are kill switches. `scripts/smoke-nomina.sh` checks deterministic tests,
   tool contracts, the plugin, and the skill/profile/catalog authority chain using
   synthetic data; deployment separately probes the dedicated runtime.

## Workflow

Prepare a quincena, review the cash totals and loan deductions, make conversational
draft adjustments, finalize the reviewed revision, then record completed payments.
Finalization reserves deductions; recording payment posts them to the loan ledger.
A skip leaves principal unchanged and resumes the ordinary installment next time.

Permanent changes are prepared and confirmed individually. Baselines require
explicit approved amounts and confirmed opening balances. No real records or
credentials ship in the repository. Contractor honorarios appear separately from
employee payroll.

The runtime root is `OWLSWATCH_PAYROLL_WORKSPACE`, conventionally
`~/.openclaw/workspace-nomina-nomina`. Keep its `state`, snapshots, exports, secrets,
configuration and virtual environment across releases. Private Drive archiving
and encrypted consistent backups are optional configuration-bound features.
See `docs/nomina-setup.md` and `docs/nomina-design.md` in the repository.
