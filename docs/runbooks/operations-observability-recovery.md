# Agent monitoring, cost evidence and encrypted recovery

Scope is exactly the active `owlswatch`, `hotel`, `bailey-finance` and `nomina` profiles and
their configured workspaces. Luna and retired Finca Tareas are excluded. This
service sends no messages, invokes no model, polls no Telegram updates and never
restarts gateways. It adds no agent business permissions.

## Install and inspect

After the normal tests, merge and synchronize clean `main`, then run:

```sh
python3 scripts/install-operations-observability.py
python3 scripts/install-operations-observability.py --install
```

The recovery dependency is pinned in `scripts/requirements-observability.txt`.
Use the approved Python environment containing that dependency; the installer
checks availability and never installs or changes shared dependencies itself.

The first command previews the exact plan. The installer enforces the release
gate, copies versioned helpers to
`~/Library/Application Support/OwlswatchOperations/bin`, records the release,
and installs two GUI LaunchAgents. Monitoring runs every five minutes; the
bounded provider balance check runs at most hourly per profile. Backups run
every 24 hours and include an isolated restore drill. Neither job depends on a
development checkout. Existing gateway and channel observer owners remain.
These GUI jobs require the user's login session; they are not headless services.

Open `~/Library/Application Support/OwlswatchOperations/report/index.html` for
the local report. `report.json` is machine-readable. `alerts.json` records only
opened/resolved transitions (bounded to 2,000); unchanged conditions stay quiet.
Provider balance failure means **unknown**, never zero. Low-balance detection
uses the provider's availability flag or a USD balance under $5. CNY balances
remain visible without inventing an exchange-rate threshold. The helper uses
the selected profile runtime's `resolveApiKeyForProvider` and calls the documented
[`GET /user/balance`](https://api-docs.deepseek.com/api/get-user-balance/) endpoint
with a ten-second fetch timeout and a fifteen-second process bound. It does not
print credentials, error bodies, chat messages or financial document content.

Balance checks require an explicit private runtime selection at
`~/.openclaw-<profile>/runtime/active.json`. Activation writes this mode-0600
regular file atomically only after the selected runtime passes its migration
checks; staging must not write it. Its fields are `schemaVersion: 1`, `profile`,
`nodePath` and `openclawPackage`. The Node binary and SDK package must belong to
the same generation under that profile's `runtime/releases/`. Rollback restores
the prior selection along with the runtime. The monitor never searches `PATH`
for OpenClaw or infers activation from a staged release.

An intentionally retained legacy runtime can be recorded with `legacy: true`,
an explicit absolute Node binary and the service owner's
`~/.npm-global/lib/node_modules/openclaw` package. Both legacy `agents.list` and
keyed `agents.entries` configs are supported; the key is authoritative in the
new format. Missing metadata reports `runtime_unconfigured`; invalid, mixed or
cross-profile runtime metadata reports an unverified balance. Neither case
launches a fallback runtime or claims the provider balance is zero.

## What the measurements prove

The guard records content-free `business_outcome` events only after a positive
tool result includes a durable identifier. IDs are hashed. Supported outcomes
are expense drafts, quote drafts, billing Gmail drafts, reservation create
results, and acknowledged Correo intake work. Reports deduplicate each artifact
and fold revised run endings, so a retry or late failure does not inflate counts.
These are completed clerk outcomes, **not** human approvals, delivered replies,
or coverage of every workflow. Unsupported paths remain unmeasured. An agent
response that completes without a verified artifact is counted separately.
Completed responses require observed nonempty assistant text and a successful
run; a successful harness exit alone does not count. This observation still
does not prove Telegram delivery, and older runs lack it.

Latency is the completed-response duration from the guard. Cost estimates can
be supplied with `--rates /private/approved-rates.json`; the file maps exact
`provider/model` names to `low` and `high` USD-per-million prices for `input`,
`output`, `cacheRead`, and `cacheWrite`. No legacy alias is silently mapped to a
new model. This preserves honest cost coverage when prices or model names
change. The report shows cost per verified outcome only when every observed
usage attempt has a known rate. It is an estimate, not a provider invoice; it
excludes external tool fees and unmeasured human review time. Historical data
predating outcome instrumentation cannot prove task counts. Journals rotate;
the report includes the oldest retained timestamp rather than pretending to
have a full month of data.

## Encrypted backups and restore drills

`recovery-keychain.swift` uses Apple's Security framework to generate a random
256-bit key and retain it in the current user's Keychain under service
`com.owlswatch.agent-recovery`, account `runtime-recovery-v1`. The key never
appears in shell arguments, output, source, or archives. The helper's `read`
operation is for the backup process's captured pipe only; do not run it in a
terminal. Encryption uses installed `cryptography` AES-256-GCM with a random
nonce, authenticated format header and authentication tag. No custom cipher is
implemented.

```sh
python3 scripts/agent-recovery.py backup \
  --keychain-helper "$HOME/Library/Application Support/OwlswatchOperations/bin/recovery-keychain" \
  --output-dir "$HOME/Backups/owlswatch-agents/encrypted-runtime"
```

Each archive contains profile configuration, local credentials and durable
state, plus configured agent workspaces. Secrets remain inside authenticated
ciphertext. Generated dependencies/caches, existing backups, lock files and
symlinks are excluded; external symlink targets and secrets stored outside these
roots are not backed up. Dependencies can be rebuilt from the recorded release.
Each SQLite database is copied with the online-backup API and integrity checked,
including committed WAL data. Inactive migrated databases that cannot open in
normal read-only mode are read as immutable only when no WAL, SHM or rollback
journal exists and the source remains unchanged during capture. Regular files changing during capture fail the
snapshot rather than silently mixing versions. There is no atomic transaction
across all files, databases and external services.

Workspace discovery supports both legacy lists and keyed agent entries,
including an inherited `agents.defaults.workspace`. Empty or ambiguous agent
inventories fail the backup rather than silently omitting workspaces. Every
workspace must remain inside that profile's approved workspace namespace.

Every backup is immediately decrypted into an owner-only temporary directory,
restored as files under a new isolated tree, and checked against an authenticated
per-file SHA-256 manifest. SQLite integrity is checked again. No runtime boots,
production files are overwritten, or messages are sent. Plaintext staging and
restore trees are removed. A `.verification.json` records file counts, integrity
results and the encrypted archive hash, without credential content.

To repeat a drill, use `restore-drill --archive /absolute/archive.owlrec` with
the same keychain-helper/output arguments. Authentication failure, unsafe archive
paths, changed files and missing keys fail closed. Investigate a failed nightly
job through its local stderr/stdout logs; do not treat an archive without a
passing verification record as a tested recovery copy. The archive ceiling is
10 GiB. Retention is manual so this tool never deletes a recovery point.

## Hardware-loss recovery remains a separate custody step

A local Keychain key and local encrypted archive protect local recovery, but
do not prove recovery after losing this Mac. Export requires an explicit path:

```sh
python3 scripts/agent-recovery.py export-key \
  --keychain-helper "$HOME/Library/Application Support/OwlswatchOperations/bin/recovery-keychain" \
  --output-dir "$HOME/Backups/owlswatch-agents/encrypted-runtime" \
  --export-path /absolute/private/recovery-key
```

The export is an exclusive-create, mode-0600 raw key file. Move it into separately
controlled offline storage and retain encrypted archives off-device through an
approved destination. Neither export nor external upload happens automatically.
The drill reports `offDeviceRecoveryKeyVerified: false` until this custody is
actually verified. Never email, paste, or commit the key. Do not reuse another
application's encryption key.

Before real disaster recovery, rebuild the recorded runtime/dependencies,
restore into an isolated location, verify the archive, and reconcile uncertain
provider/Gmail/government outcomes before reconnecting business writes. Restore
drills intentionally cannot activate a second Telegram consumer.
