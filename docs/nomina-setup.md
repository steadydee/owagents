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

Payroll remains on demand with idle model heartbeats disabled. The separate,
operator-installed maintenance LaunchAgent can check health and retry configured
archives without a user message; it never schedules payroll actions. OpenClaw's
built-in channel health monitoring and launchd own gateway liveness. Maintenance
does not restart the gateway or touch other agents, Finca, Luna or their watchers.

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

## Native review and private downloads

The manager opens the server-issued `/revisar_nomina TOKEN` or its **Revisar**
button. Rendering alone no longer records review. The host checks the rendered
SHA-256, sends the complete review through OpenClaw's durable Telegram sender,
and requires `sent` plus valid platform message receipts. Only then does the
internal `review-delivered` call revalidate the actor, token, current version and
hash and save the review marker. A separate **Confirmar** button follows that
gate. Partial, failed, suppressed, receipt-less or changed reviews cannot offer
confirmation. Receipts establish host delivery evidence, not human readership.
The manager still explicitly uses `/confirmar_nomina TOKEN`; plain "sí" and
model-written approval fields have no authority.

`nomina_export` returns `download_commands.csv` and `download_commands.html`.
Use those exact `/informe_nomina RUN_ID csv` and `/informe_nomina RUN_ID html`
commands, presented as **Descargar CSV** and **Descargar HTML** buttons where
available. The native command rechecks the authorized private route, requires
a non-draft run, selects a contained private export on the server and sends it
as a document to that route. It accepts no file path or alternate recipient.
Drive setup is not required for this Telegram download. Export success or a
local path must not be described as successful attachment delivery; the native
sender checks its receipt and reports uncertainty on failure. JSON remains an
export/archive format, not an accepted native download argument. No XLSX or
native PDF is generated; a human may print the HTML to PDF.

## Private archive and recovery

Private off-machine setup remains pending the user's choice of destination,
access and independent key custody. Do not claim it is active because the source
is installed, local reports exist or maintenance runs. Archiving is optional and
starts disabled. The required archive setup fields are:

```json
{
  "archive": {
    "enabled": false,
    "drive_folder_id": "<dedicated-private-nomina-folder-id>",
    "google_credentials_file": "secrets/nomina-google.json",
    "allowed_reader_emails": ["<approved-owner-email>", "<dedicated-uploader-email>"],
    "backup_key_file": "secrets/backup.key"
  }
}
```

Replace the placeholders only in private runtime configuration after the user
chooses the destination and approved principals. `archive.drive_folder_id` pins
one dedicated folder. `archive.google_credentials_file` points to a private
contained JSON file (mode `600`) dedicated to Nómina, with credential `type`
`service_account` or `authorized_user`. The latter is an authorized-user OAuth
credential file with the refresh authorization for a dedicated app, not merely
an OAuth client-secret download. The uploader uses the narrow `drive.file` scope.
Never copy, share or repurpose another agent's Google credential/token files;
never broaden scopes to bypass an inaccessible folder.

- For `service_account`, choose a private folder in a Shared Drive that supports
  that dedicated service account's uploads. Service accounts have no personal
  storage quota; this is not the personal Drive path.
- Personal Drive is possible only with dedicated `authorized_user` OAuth and a
  folder created by, or explicitly authorized for, that app. An arbitrary folder
  visible in the user's Drive is not sufficient under `drive.file`. The user must
  choose and authorize this setup separately; existing other-agent consent and
  credentials are not shared. Dedicated OAuth may also use an appropriately
  authorized Shared Drive folder.

`archive.allowed_reader_emails` is a required explicit, nonempty allowlist of
individual email principals, not a sharing operation. Include every intended
owner, writer/reader and uploader that appears in effective folder permissions,
including the service-account email when that option is used. Every direct or
inherited permission must be an individual `user` with an email on the allowlist.
Groups, domain grants, public/anyone grants, unlisted or unidentifiable principals,
missing allowlists and incomplete permission inspection fail closed. Do not use
wildcards or group addresses to widen access. The archive cannot select another
destination or change sharing; an operator must correct the folder ACL or choose
a suitably isolated folder. Verify this permission boundary in the integrated
release before enabling any live archive. `ARCHIVE_READERS_REQUIRED` means the
reader allowlist is missing/invalid; `ARCHIVE_CREDENTIAL_INVALID` rejects an
unsupported credential type; `ARCHIVE_FOLDER_NOT_PRIVATE` means the permission
check did not establish the required individual-only access.

See Google's
[Shared Drive guidance](https://developers.google.com/workspace/drive/api/guides/about-shareddrives).
Privacy checks use the paginated permissions endpoint because the
[file resource](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)
does not populate embedded permissions for Shared Drive items.

Generate a Fernet key locally using the pinned Python environment, save it to
`archive.backup_key_file` with mode `600`, and retain an independent secure copy.
Do not print or paste it into a chat, commit it, or store the only copy beside
the database backup. Set `archive.enabled:true` only after both credentials and
the key are available and the explicit reader allowlist/private ACL checks pass.
No live archive activation is authorized until the user chooses this setup.
Install the pinned package requirements even when initially staging with
archiving disabled, so the same release has a verifiable environment.

Configured archives report local encryption and off-machine upload versions
separately. `last_uploaded_at` is the last completely uploaded job's Unix timestamp
and `last_upload_age_seconds` its nonnegative age; both are `null` before a job
completes. `off_machine_current` must be checked against the current state version.
`report_links` contains at most 30 distinct uploaded JSON/CSV/HTML report paths,
each with `job_id`, `state_version`, `relative_path`, `file_id` and `url`;
`report_links_truncated` signals more. Already-uploaded reports from an incomplete
job may appear, but encrypted database backups never do. These are existing
private Drive links, not public sharing grants or proof of present reader access.

Use `nomina_archive_status` with `{"retry":true}` for a manual attempt. Independent
maintenance also repairs the commit-to-enqueue gap and attempts at most two jobs
per invocation. Each `Archive.retry()` handles one job with at most four files,
persists remote IDs before creation, and returns `uploaded`, `idle` or `busy`.
A process lock prevents overlapping uploads and releases after a crash. Local
corruption/conflicts fail closed; retrying an upload never replays payroll.

Restore is an operator-only offline operation into a fresh private directory.
Use the package's `archive.restore_backup(encrypted_path, key_path, destination)`
function with locally held paths; never pass backup keys through an agent tool.
The equivalent offline CLI is:

```sh
python3 -B tools/owlswatch_payroll/server.py restore-backup "$BACKUP_FILE" "$BACKUP_KEY_FILE" "$NEW_RESTORE_DIRECTORY"
```

Choose a new destination in a private operator-controlled location, never the live
workspace. Restore authenticates the encrypted bundle, exact archive members,
manifest authority, database hash, schema/state version, SQLite integrity and
foreign keys before publishing. Invalid backups leave no destination; an
existing destination is not overwritten. Keys, configuration and credentials
must be retained/reconstructed separately. Retain
the restored copy separately, reconstruct private configuration/provider auth,
verify payroll/loan history with the channel disabled, then deliberately cut over.
Never restore over a running ledger or run two gateways against the same ledger.

The implemented recovery tests use synthetic ledgers, fake Drive uploads and
damaged backups. They are not a live Drive upload/access check or a live-provider
restore drill. A future private setup must verify those separately before
claiming off-machine protection. No production business write is needed to test
this code or to review these instructions.

Credential rotation: disable the affected profile/channel, replace only the local
bot/provider/service-account credential, verify configuration and authorized/denied
routes, then resume. For backup-key rotation retain old keys for old backups and
verify a recovery copy under the new key before retiring anything. Never erase
older backups to conceal a rotation failure.

## Independent maintenance and login recovery

After deploying reviewed source and its private Python environment, an operator
can run the following on the gateway host from the reviewed release checkout:

```sh
python3 -B scripts/install-nomina-maintenance.py --workspace "$OWLSWATCH_PAYROLL_WORKSPACE" --enable --reload-gateway
```

This is a separate operator installation, not a model tool or an automatic result
of staging source. It requires the existing `ai.openclaw.nomina` gateway plist
and installs only `ai.openclaw.nomina.maintenance`, with `RunAtLoad` and a
300-second interval. The private workspace file `maintenance.enabled` is the
maintenance kill switch; removing or renaming it stops work on subsequent runs.
Omitting `--enable` does not disable an already-present enable file. Telegram,
payroll and archive settings remain separate; this command does not select a
Drive folder, create credentials or activate off-machine backup.

The installer preserves the gateway's environment, sets its `RunAtLoad` and
`KeepAlive`, and redirects stderr into private logs. The explicit
`--reload-gateway` option reloads only this gateway so launchd picks up its new
stderr path. Without that option it reports `gateway_reload_required:true`;
an ordinary kickstart is not sufficient to reload plist changes. Both
plists are user LaunchAgents in `gui/<uid>`: after reboot, the owning user must
log in. There is no system LaunchDaemon, pre-login service or unattended cold-boot
guarantee. Test logout/reboot/login recovery separately before relying on it.

Maintenance validates the isolated profile/workspace, takes a nonblocking lock,
and persists outcomes in `state/maintenance.sqlite3`. Its bounded duties are:

- Probe `openclaw --profile nomina channels status --json` with a 30-second timeout;
  warn after 15 minutes of unverified channel health. Never restart the gateway.
- Read the existing ledger version, enqueue a missing recovery copy and attempt
  up to two upload jobs. Transient failures back off from five minutes to one
  hour. Non-retryable failures queue an attention notice and use an hour cooldown;
  changing archive settings resets the upload cooldown. It never creates a
  missing payroll ledger or repeats a business action.
- Group authorized journal entries still unverified after 10 minutes into at
  most one warning per hour. Ask the manager to inspect current state, never to
  blindly repeat changes or payments.
- Attempt at most one fixed-text notice per run, only to the single configured
  route for account `default`, with matching Telegram/payroll sender allowlists.
  Claim the notice before sending; unknown outcomes are not resent. Only a
  known Telegram 429 response gets delayed retries, no more than three attempts
  total, at least an hour apart. A Telegram outage can also prevent its warning.

The private `state/delivery.sqlite3` journal stores a hashed inbound identity and
reply digest, plus route/sender/session/run IDs, timestamps and statuses; it does
not retain financial message bodies or raw provider exceptions. Hashes are not
encryption and the metadata is still sensitive. Host hooks must supply a trusted
source message ID and session; the tool context still lacks that source ID.
Ordinary outbound hooks have no source/run ID. The source-bound final-reply hook
therefore uses the SDK durable sender as the sole delivery owner and records its
receipt on the exact inbound row. It suppresses a second core send, including on
uncertainty. It never infers delivery from matching text. Missing host metadata
falls back to normal core delivery without a delivery claim. Confirmation results
are journaled before execution and sent durably. Review content must also match
the SDK's effective normalized text before the review is recorded.
A successful generation or native journal marker is not a substitute
for the native review receipt/hash gate. This is not complete message deduplication,
a guaranteed reply audit or a financial replay queue. No automatic journal-row
retention/purge is implemented.

Logs are private under `~/.openclaw-nomina/logs`. Maintenance rotates its scoped
gateway/maintenance logs at 5 MiB, keeping three numbered copies while retaining
the active inode. This copy/truncate rotation is best-effort and can lose a
concurrent final line; it is not lossless audit storage or journal retention.
Preserve delivery and maintenance SQLite state across source deployments.

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

### Conversation evaluations

Run from the integrated release checkout; the case catalog and evaluator are
`tools/owlswatch_payroll/tests/conversation-cases.json` and
`scripts/eval-nomina.py`. Offline commands require no provider credentials:

```sh
python3 -B scripts/eval-nomina.py --list
python3 -B scripts/eval-nomina.py
python3 -B scripts/eval-nomina.py --case draft-spanish --case report-native-download
python3 -B -m unittest discover -s tools/owlswatch_payroll/tests -p 'test_conversation_eval.py' -v
```

Default mode uses scripted fixtures, not a model. It verifies the harness, real
server tool transport against isolated synthetic state, deterministic invariants
and failure tripwires. Actual model evaluation requires an explicit paid opt-in:

```sh
python3 -B scripts/eval-nomina.py --live --case draft-spanish --max-calls 6 --timeout 120
python3 -B scripts/eval-nomina.py --live --runtime-config "$PRIVATE_OPENCLAW_CONFIG" --max-calls 6
```

The live mode reads `DEEPSEEK_API_KEY` from the runtime environment, or falls back
to the explicitly supplied private config outside git. It never discovers or
loads credentials in fixture mode. Do not paste keys into commands, chat or the
repository. `--case` is repeatable; omitting it runs all cases. The fixed provider
model is `deepseek-reasoner`, without silent fallback. The default and maximum
budget is six HTTP attempts per scenario including retries, not six for the
whole suite; `--timeout` defaults to 120 seconds and is capped at 180.

Both modes use temporary synthetic workspaces outside git, with archive disabled
and server subprocess network/process access denied. Native approval is not
available to the evaluated model. Live mode means real provider calls, not live
payroll, Telegram or Drive operations. Complete synthetic traces, including
provider reasoning when present, stay in private runtime files (directories
`700`, files `600`); stdout contains aggregate counters and the trace directory.
Review and then remove those traces locally, never commit them.

Exit status `0` means deterministic checks passed, `1` means scenario failures,
and `2` means configuration failure. Lexical checks are conservative tripwires,
not semantic proof. `human_review_pending` and the unscored rubric remain in both
modes. Record mode, case selection, model, source hashes, counters and human
review separately. A passing fixture run is not a measured model-quality result;
neither mode establishes live delivery, recovery or production acceptance.

### Deploy reviewed source

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
   native review/confirmation. Confirmation without a delivered, recorded native
   review must fail. `/revisar_nomina TOKEN` must render exact saved amounts and
   the complete destination when relevant. Verify full delivery precedes the
   separate confirmation control; inject partial send, absent receipt, changed
   hash and a state change during delivery and confirm that each fails closed.
   Model text and ordinary tool arguments cannot mark reviewed or approve.
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
   escaping and HTML escaping. Exercise both native download buttons without
   Drive setup; reject a draft, a different route and an escaping path. A failed
   document send must not be described as delivered.
8. With optional archives enabled, interrupt upload and retry the same remote IDs;
   payroll must not execute again. Restore the encrypted consistent backup into
   a fresh offline directory and reconcile balances/history before accepting it.
   Reject damaged authenticated backups without a partial destination and preserve
   an existing destination. Record synthetic and live-provider evidence separately.
9. In an isolated maintenance fixture, test overlap, backoff, stale delivery
   notices, prolonged channel failure and unknown notice-send outcomes. Confirm
   that no financial request or uncertain notice is replayed. Verify the enable
   file and dedicated LaunchAgents separately; reboot recovery requires login.

After setup passes, confirm each real profile and actual opening balance with the
manager. Reconcile the first production preview with the agreed baseline before
the manager finalizes it. Neither projected loan schedules nor old spreadsheet
net figures can substitute for this initial confirmation.
