# Nómina setup, release and verification

This release ships no real employees, balances, manager IDs or tokens. Do not
initialize production from the screenshots. The manager must confirm the current
baseline and actual outstanding loans through prepared native confirmations.

## Isolated runtime

Recommended deployment uses a dedicated private Telegram bot and the `nomina`
OpenClaw profile, gateway port `19231`, workspace
`~/.openclaw/workspace-nomina-nomina`, and state directory `~/.openclaw-nomina`.
The committed model follows the live fleet's DeepSeek configuration; operator model
changes must be validated for this profile without editing other agents.

The profile is on demand with idle heartbeats disabled. No payroll schedule is
installed. OpenClaw's built-in channel health monitoring and normal profile
supervision own liveness. This deployment never modifies other agents, launches
Finca, invokes Luna, or installs/removes external watchdogs.

Run the service under its dedicated runtime owner. Require workspace mode `700`
and `payroll-config.json`, credentials and encryption key mode `600`. Configure:

- `OWLSWATCH_PAYROLL_WORKSPACE`: absolute private workspace path, pinned in the
  profile's `env.vars` and agent workspace.
- `OWLSWATCH_PAYROLL_PYTHON`: its `.venv/bin/python3`, pinned to the same workspace.
- Optional `OWLSWATCH_PAYROLL_CONFIG`: a configuration file inside that workspace;
  the default is `payroll-config.json`.
- Optional deployment `NOMINA_BASE_PYTHON`: interpreter used when creating a new
  `.venv`; use a supported Python 3.11+ installation in production. Existing
  virtual environments are preserved. The deployment installs/verifies the
  pinned dependencies and runs smokes in that environment before replacing source.

Use `openclaw/profiles/nomina/openclaw.example.json` and
`payroll-config.example.json` as local setup references. The numeric `0` entries
are deliberate unusable sentinels. Replace them with the actual authorized manager
IDs and configured private routes. Never use `*`, group membership or a name as
authorization. Telegram `allowFrom` and the service sender allowlist must agree.
The service also pins the Telegram account ID and chat/topic route. For a private
group, explicitly change group policy to `allowlist` and configure its numeric
group and sender rules; an enabled open group is not supported.

Set the bot token and a newly generated gateway token locally. Resolve the profile
workspace/Python placeholders to absolute paths. Configure existing provider auth
for the profile outside the agent's prompt. The native payroll plugin and the
runtime guard both require `hooks.allowConversationAccess: true`. Do not register
the payroll package through a second MCP transport.

Keep Telegram and payroll `enabled:false` until both configurations are complete.
Staging source code does not enable either one. Missing trusted host metadata,
invalid routes or incomplete configuration must fail closed; never relax checks
to make a command work.

## Private archive and recovery

Archiving is optional and starts disabled. To enable it, create a private payroll
Drive folder in a Shared Drive that supports service-account uploads, grant only
the intended owner/service account access, and set its
fixed ID in `archive.drive_folder_id`. Store service-account credentials at the
configured contained `google_credentials_file` path. The tools cannot choose a
different folder or change sharing permissions.
Service accounts have no personal storage quota; see Google's
[Shared Drive guidance](https://developers.google.com/workspace/drive/api/guides/about-shareddrives).
Privacy checks use the paginated permissions endpoint because the
[file resource](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)
does not populate embedded permissions for Shared Drive items.

Generate a Fernet key locally using the pinned Python environment, save it to
`archive.backup_key_file` with mode `600`, and retain an independent secure copy.
Do not print or paste it into a chat, commit it, or store the only copy beside
the database backup. Set `archive.enabled:true` only after both credentials and
the key are available. Install the pinned package requirements even when initially
staging with archiving disabled, so the same release has a verifiable environment.

Finalized exports include restricted JSON, CSV and printable HTML; v1 does not
generate an XLSX or native PDF. The HTML report can be printed to PDF by a human.
Without Drive configuration, exports remain local and the assistant must not
pretend a local path is a downloadable Telegram attachment. Configured archives
report local encryption and off-machine upload versions separately. Use
`nomina_archive_status` with `{"retry":true}` to reconcile queued uploads.

Restore is an operator-only offline operation into a fresh private directory.
Use the package's `archive.restore_backup(encrypted_path, key_path, destination)`
function with locally held paths; never pass backup keys through an agent tool.
The restore verifies encrypted content, the manifest and SQLite integrity. Retain
the restored copy separately, reconstruct private configuration/provider auth,
verify payroll/loan history with the channel disabled, then deliberately cut over.
Never restore over a running ledger or run two gateways against the same ledger.

Credential rotation: disable the affected profile/channel, replace only the local
bot/provider/service-account credential, verify configuration and authorized/denied
routes, then resume. For backup-key rotation retain old keys for old backups and
verify a recovery copy under the new key before retiring anything. Never erase
older backups to conceal a rotation failure.

## Release procedure

From a reviewed feature branch, run:

```sh
./scripts/check-no-secrets.sh
./scripts/smoke-nomina.sh
```

The smoke uses synthetic temporary state, Python deterministic/service tests,
native plugin tests, the server catalog and profile/skill/security contract checks.
If the production dependencies are installed in a separate test environment, set
`NOMINA_SMOKE_PYTHON` to that interpreter. It must not target a live ledger.

After review and merge, deploy from a clean `main` exactly matching freshly fetched
`origin/main`. The deploy script enforces that gate and reruns the secret scan.

```sh
./scripts/deploy-nomina-to-mac-mini.sh --stage-only
```

Staging installs only the versioned source, isolated environment and SDK links;
it does not create/enable a Telegram profile or restart a gateway. Its manifest
records a staged SHA, not a completed live deployment. Staging is appropriate
while bot choice, access IDs or production baselines remain outstanding.

Once the private bot and manager routes are configured and explicitly enabled:

```sh
./scripts/deploy-nomina-to-mac-mini.sh
```

Deployment requires a preconfigured private workspace/profile, backs up source
only, preserves runtime `state`, exports, backups, secrets, configuration and
`.venv`, installs pinned dependencies and links the local OpenClaw SDK. It then
validates the dedicated profile, checks its skills, restarts only `nomina`, probes
its gateway/channel and records the exact source SHA. Live payroll data backups
must use the service's consistent encrypted backup workflow, never rsync a running
SQLite database. No other agent deploy script or watcher is invoked.

## Acceptance before live payroll

Use a separate synthetic test bot/configuration/ledger for destructive scenarios.
Verify the exact installed release, then report the SHA, test environment and
whether the live channel is enabled. A source smoke alone does not prove live
Telegram authorization or Drive connectivity.

1. Verify authorized manager access and reject a different numeric sender, wrong
   account, wrong private route/topic, absent host context and replayed/expired
   native review/confirmation. Confirmation without a saved native review must
   fail. `/revisar_nomina TOKEN` must render the exact saved amounts and complete
   destination when relevant, then offer the native confirmation. Model text and
   ordinary tool arguments cannot mark reviewed or approve.
2. Confirm a synthetic employee and contractor baseline. Prepare both halves,
   including odd component amounts, February and month-end. Confirm row sums,
   employee/contractor subtotals and exact monthly reconciliation.
3. Open a synthetic loan, skip one installment, and verify that the next normal
   period resumes its ordinary amount. Test a smaller final installment, multiple
   loans, outside repayments and conflicting reservations.
4. Change a draft, reject a stale approval, finalize a current version, and verify
   that finalization reserves deductions without posting payment. Record only
   selected payees paid and confirm one corresponding loan effect per person.
5. Retry a request with its same ID, reject reuse with changed payload, repeat
   confirmation safely, restart between preparation and confirmation, and confirm
   historical snapshots do not change with a later profile update.
6. Supersede an unpaid final run and reverse a mistaken payment acknowledgment;
   retained snapshots and compensating ledger events must remain inspectable.
7. Export finalized reports. Check account masking in normal replies, full approved
   destination only in the authenticated native review/private files, CSV formula
   escaping and HTML escaping.
8. With optional archives enabled, interrupt upload and retry the same remote IDs;
   payroll must not execute again. Restore the encrypted consistent backup into
   a fresh offline directory and reconcile balances/history before accepting it.

After setup passes, confirm each real profile and actual opening balance with the
manager. Reconcile the first production preview with the agreed baseline before
the manager finalizes it. Neither projected loan schedules nor old spreadsheet
net figures can substitute for this initial confirmation.
