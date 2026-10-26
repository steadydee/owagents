# Isolated OpenClaw runtime staging and migration

`scripts/prepare-isolated-runtime.py` prepares a private, immutable runtime
bundle and a profile-specific migration plan. **It cannot activate a runtime.**
The `activate` command always refuses before inspecting a profile. This is
intentional: OpenClaw 2026.10.5 changes config, authentication and durable-state
schemas, so restoring an old LaunchAgent alone is not a safe rollback.

The staging implementation is ready for review; the activation procedure below
requires separate implementation and proof. No live migration is implied by a
successful stage or by a synthetic provider canary.

## Scope and supervisor ownership

Only these profiles are accepted:

| Profile | Existing supervisor owner | Scheduled callers covered by the plan |
| --- | --- | --- |
| `owlswatch` | `gui/<home-owner-uid>` | Telegram observer and Correo poll, summary, unanswered jobs |
| `hotel` | `system` | Telegram observer, daily summary, Registro pickup |
| `bailey-finance` | `gui/<home-owner-uid>` | Telegram observer |
| `nomina` | `gui/<home-owner-uid>` | Payroll maintenance |

Hotel's duplicate user LaunchAgents are listed as untouched. They must remain
off. No shared npm or Homebrew installation is changed. Luna and retired Finca
are excluded. The script does not invoke launchctl, a gateway, a business tool,
or a provider API. It does not write a live config or change an existing SDK
symlink.

The plan retains the gateway's existing environment wrapper, env-file argument,
gateway flags and port. For the known auxiliary jobs it replaces an explicit
shared `openclaw` argument and sets `OPENCLAW_BIN` to a scoped CLI. Correo's
runner and Hotel's scripts already honor that environment variable. Nomina's
maintenance job has an explicit CLI argument. The wrapper fixes the profile,
state and config environment and rejects conflicting profile/state flags. It is
a routing aid, not a sandbox or an approval boundary.

Each configured plugin must resolve inside that profile's enumerated workspace
or state roots. Its planned `node_modules/openclaw` link points to the same
isolated package as its gateway. Real embedded SDK installations, unknown
plugin roots, workspace symlink redirects, missing gateway plists, and unknown
gateway invocation shapes require explicit review instead of guessed changes.

## Read-only plan

Candidate layout is fixed to:

```text
<candidate-root>/node-v24.22.1-darwin-arm64/bin/node
<candidate-root>/openclaw-2026.10.5/package-lock.json
<candidate-root>/openclaw-2026.10.5/node_modules/openclaw/
```

Create the private plan outside git. It contains local paths, service labels,
hashes, and existing SDK targets; it does not include config values or secrets.

```sh
umask 077
python3 scripts/prepare-isolated-runtime.py plan \
  --profile owlswatch --candidate-root "$RUNTIME_CANDIDATE_ROOT" \
  > "$PRIVATE_PLAN_PATH"
```

`--home` exists for disposable test homes and controlled operator use. Production
use must select the existing service account's canonical home, not root's home.
The script derives the GUI uid from that home rather than assuming a uid.

## Gated staging

First verify the Node download against an independently obtained official
checksum and review the npm registry integrity and lockfile. The npm integrity
field records the OpenClaw tarball; it does **not** prove the complete installed
dependency tree. The plan's separate tree digest pins every installed file,
relative symlink and executable flag. An operator must approve the complete
candidate tree after provider/plugin checks. Do not treat a self-computed hash
as independent provenance.

Run staging only from clean `main` exactly matching freshly fetched
`origin/main`. The script enforces `assert-release-ready.sh` before creating
runtime directories or executing the candidate binary. Supply the independently
reviewed Node hash, approved installed-tree hash and npm integrity string:

```sh
python3 scripts/prepare-isolated-runtime.py stage \
  --profile owlswatch --candidate-root "$RUNTIME_CANDIDATE_ROOT" \
  --node-sha256 "$APPROVED_NODE_SHA256" \
  --npm-tree-sha256 "$APPROVED_NPM_TREE_SHA256" \
  --npm-integrity "$APPROVED_OPENCLAW_INTEGRITY"
```

Only after these values match does the script execute the candidate Node binary
with a clean environment and a ten-second timeout to verify version, macOS and
arm64. It copies Node and the complete installed dependency tree into a private
temporary directory under the selected profile, verifies the copied hashes,
then atomically renames the bundle into `runtime/releases/`. Snapshot plists
and the plan have mode 0600; the release directory and scoped CLI have mode
0700. Source runtime files, SDK links, schedules and gateway processes remain
unchanged. A failed copy removes the partial staging directory and preserves
older releases. Identical repeated staging returns `already_staged`; changed
content or a changed profile plan refuses to overwrite the immutable release.

The staged `snapshots/` directory holds original and proposed plist bytes. It
may contain environment values, so it is private state and must never enter
git or a report. This is a preparation snapshot, **not** a full-state backup.
No credential is copied from one domain to another.

## Activation gates and required future implementation

Do not bypass the disabled `activate` command. Implement and review these gates
before live activation, one profile at a time:

1. Record the exact published source SHA, approved Node checksum, installed-tree
   digest, npm integrity, runtime versions and canary evidence. Lifecycle scripts
   may have been suppressed by npm; successful `--version` is insufficient.
   Verify required bundled providers, native dependencies and each custom plugin
   without installing unreviewed scripts into the shared runtime.
2. Make a private disposable copy of the selected profile and its own
   workspaces. Disable channels, schedules and real business transports. Run the
   supported migration there, never on live state as a diagnostic. Inspect
   routing, human sender/group allowlists, tool policies, auth scope, queues,
   sessions, media paths, plugin hooks and scheduled callers before and after.
3. Prove provider success, forced retry/fallback and synthetic tool invocation.
   Check the tool registration contract and `minimal` plus explicit `alsoAllow`
   behavior in the new runtime. Verify native approval behavior in the Hotel
   fixture. Canary success on Cuenta alone is not evidence for other domains.
   The installed new runtime defaults may retry transient failures for longer
   than a turn deadline: validate per-agent `settings.json`
   `retry.provider.maxRetries` and `maxRetryDelayMs` together with the model
   fallback config and bounded total turn duration. Never infer these settings
   merely from config acceptance.
4. Verify all source consumers of the old schema are compatible. Known readers
   at the initial migration review: `configure-cuenta-intake.mjs`,
   `harden-runtime.py`, `deploy-nomina-to-mac-mini.sh`, `deploy-cobros.sh`,
   `tools/owlswatch_payroll/maintenance.py`, both Cuenta and Cobros UAT scripts,
   and Cuenta/Nomina smoke examples. The migration replaces `agents.list` with
   keyed `agents.entries` and the legacy default model allowlist with
   `modelPolicy.allow`. Legacy `auth-profiles.json` requires migration to
   SQLite-backed agent auth. Do not delete legacy auth files to silence an
   error without proving the migrated provider identity and rollback.
5. Capture the exact launchd loaded and disabled states, owner domain, service
   arguments, environment wrappers and current versions. Capture config/plist
   and SDK link hashes again and compare with the reviewed plan. Refuse drift.
   Quiesce only the selected profile's eligible schedules and gateway, without
   enabling disabled jobs or Hotel's unused GUI services. Prevent new work
   during the checkpoint and switch; do not interrupt a side effect in flight.
6. Make an encrypted full-state checkpoint of the profile, agent dirs,
   workspaces, media/spools, auth and relevant service files. Use the recovery
   tool only after its code is published. Restore the snapshot into an isolated
   directory, verify its manifest hashes and demonstrate that the old runtime
   can open the restored state without channels or side effects. Confirm the
   recovery key is available. Local Keychain recovery alone does not establish
   hardware-loss recovery; the offline recovery-key copy is a separate action.
7. Install reviewed migrated config/auth/state, SDK links and supervisor plists
   transactionally with a durable activation journal. Use same-filesystem atomic
   file/link replacements. If any preboot step fails, restore exact original
   files/links and loaded/disabled states. After any new-runtime boot, rollback
   must restore the full checkpoint before starting the old runtime. Never
   replay an uncertain external side effect as part of rollback.
8. Start exactly the recorded supervisor owner. Verify version, authenticated
   local gateway health and a synthetic no-delivery model/tool canary. Check
   response completion, turn limits and fallback time, not just a live process.
   Resume only previously enabled schedules, after their scoped CLI/config
   compatibility passes. Record the deployed SHA and runtime hashes. Preserve
   the checkpoint and previous runtime until the observation window succeeds.

Unknown schedules, plugins or supervisors are blockers, not permission to scan
or modify another domain. Global npm links and shared Node installations must
remain untouched throughout activation and rollback.

## Verification

```sh
python3 -m unittest discover -s tests -p 'test_isolated_runtime.py' -v
./scripts/smoke-platform.sh
./scripts/check-no-secrets.sh
```

The tests use synthetic files, disposable homes and mocked release/Node checks.
They cover scope refusal, path/symlink escape, Hotel ownership, unchanged live
fixtures, release refusal before execution, wrong hashes, idempotence,
tamper refusal, partial-copy cleanup, bounded Node verification, and scoped CLI
routing. They do not claim a live migration or full-state recovery has passed.
