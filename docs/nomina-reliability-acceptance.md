# Nomina reliability acceptance - 2026-10-07

Scope: only Nomina code, private report delivery, operator maintenance, archive
retry/restore, and conversational guidance. No new payroll authority or Operations
connection. No real payees, payroll runs, payments or transfers were created.

## Evidence

- 145 Python tests and 39 JavaScript tests passed against the installed OpenClaw
  SDK 2026.7.1-2. These use isolated synthetic data and injected delivery failures.
- 18/18 offline conversation fixtures passed. They exercise real local tools but
  do not score DeepSeek's ability to understand natural language.
- Live DeepSeek evaluation stopped with HTTP 402 (insufficient balance), before
  a model answer or any tool action. Live conversation quality remains unverified.
- Cuenta, Cotiza, Correo, Cobros, Hotel and Finca regression smokes passed with
  temporary workspaces. Existing OW configuration validation/bindings were checked.
- Secret scan and git diff whitespace checks passed.
- Review found and corrected transformed-review approval, missing late run
  correlation, unreliable hash-only acknowledgments, lost confirmation replies,
  dropped additional final payloads and metadata-only topic handling.

## Release Verification

Deploy only the merged, clean origin/main commit with the Nomina deploy script.
Its runtime source manifest records the exact deployed SHA and hashes. Verify
both launchd jobs, the gateway/channel, the loaded stderr path and maintenance's
safe output. LaunchAgents require the `agent` user to log in after reboot.

Production read-only delivery checks must be recorded separately from the
synthetic tests. Do not run a fake payroll to test production. Real native
review/confirmation and report-download UAT still requires a reviewed synthetic
test context or the user's first genuine approved setup.

The first live gateway reload encountered transient launchd exit 5 during
bootout/bootstrap. A retry restored the gateway. The installer now retries only
that bootstrap error, at most four attempts, without touching another service.

## Remaining External Gates

1. Refill the DeepSeek account, then rerun `scripts/eval-nomina.py --live` using
   the explicit private Nomina runtime configuration. Record provider failures
   separately from behavior failures and inspect the private synthetic traces.
2. Choose and authorize a private Drive owner/folder, configure dedicated uploader
   credentials and exact reader emails, and retain the encryption key separately.
   Verify a live encrypted upload/download/restore before calling backup active.

Archive code and independent retry are implemented. The live archive remains
disabled until those access and key-custody steps are complete. Local synthetic
restore tests are not proof of off-machine recovery. No other agent is restarted
or reconfigured by this release.
