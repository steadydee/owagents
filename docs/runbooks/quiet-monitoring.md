# Quiet monitoring and retained task outcomes

Scope: Owlswatch, Hotel and Ledger. Luna is excluded. Finca is retired and
Nómina is paused while payroll moves to an outside provider. Do not deploy,
schedule, probe or reactivate either inactive agent. Preserve their data.

## Default behavior

- The aggregate report runs every five minutes using local files only.
- Provider balances run once daily at 08:15 on the Mac's local clock (the
  production Mac uses America/Bogota). No run-at-load balance request.
- No scheduled Vercel, Telegram `getMe`, or model canaries.
- The old external observer files may be installed for recovery of historical
  evidence, but their launchd services remain disabled. GUI installation and
  Hotel headless installation/uninstallation cannot re-enable them.
- Gateways remain owned by launchd; built-in channel recovery retains ownership
  of channel restarts. The aggregate does not restart workers or send messages.

Nómina's gateway and maintenance jobs are disabled by the operator. It is also
excluded from local and remote monitoring. Retained incident history is kept;
removing a paused profile's active alert is recorded as `monitoring_paused`,
never as proof that its original task completed. Keep payroll data and private
configuration for handover; this monitoring release does not deploy payroll
source or change payroll authorization.

## Durable request accounting

The installer initializes one private fleet budget at
`~/Library/Application Support/OwlswatchOperations/monitor-state/http-budget.sqlite3`.
The helper refuses network requests if the budget is missing, corrupt, unsafe,
unwritable or exhausted. Reinstallation validates existing state; it must never
reset consumed permits. Every actual balance HTTP attempt reserves its permit
before network I/O. Redirects and automatic retries are disabled; crashes do
not refund permits.

Preserve the sibling `.initialized` marker with the SQLite ledger. A missing
ledger after initialization blocks requests and installer recreation. Never
restore an older budget snapshot to regain allowance.

The policy allows three balance attempts and two recovery attempts per rolling
24 hours, with a shared diagnostic ceiling of five and a 30-day ceiling of 150.
The recovery category is reserved capacity, not an active recovery poller.
Owner-alert accounting is a separate two attempts/day and 60/30 days. No new
owner-alert sender or Vercel recovery loop is activated by this release.
Normal customer work is not charged to this diagnostic budget and must not be
shut down when monitoring runs out of permits.

Budget exhaustion is a local `monitor:budget_exhausted` status, not evidence
that the provider or bot is down. Preserve a missing/corrupt budget for review;
do not remove it to regain permits. A deliberate manual acceptance suite has
an explicit one-off request budget and is accounted separately.

## Availability is not task completion

The local report separates observed service availability from unresolved task
outcomes. Channel snapshots have a finite freshness window; old snapshots are
unknown, not a current outage or a fresh health guarantee. An unrelated later
success cannot resolve an earlier failed employee request. Exact matching
response evidence still does not imply a financial result or delivery receipt.

The aggregate retains content-free outcome evidence in `report/outcomes.json`
under its process lock, with atomic private writes. Preserve this file across
releases, journal rotation and recovery. A corrupt retained ledger produces a
visible unknown/persistence warning and is never silently replaced by an empty
ledger. Profile-filtered reports preserve other profiles' retained evidence.

Do not replay an uncertain payment, reservation or government submission to
clear an alert. Reconcile its original correlation/idempotency key and receipt.
The old failed Hotel request is not resolved by installing this release.

## Release and verification

Run the focused monitor/maintenance tests, the platform smokes and the secret
scan. Publish only sanitized source through a PR to main. Deployment must use
the exact clean synchronized `origin/main` commit and pass
`scripts/assert-release-ready.sh`. Existing public-repository publication
approval requirements still apply; live copying is not a release bypass.

Install the observability scripts and daily schedule from that release with
`scripts/install-operations-observability.py --install --monitor-only` (this
leaves the backup and keychain jobs alone). Update observer/health
files without enabling their jobs. Verify exact launchd labels locally: all
three active gateways running, external observers disabled, Finca off, Nómina
gateway and maintenance disabled, local aggregate
without `--check-balance`, and only the daily job carrying that option.

If Hotel's system observer is still loaded, the observer installer refuses to
change its files or claim quiet mode. Use the administrator-only
`scripts/install-headless-hotel-services.sh quiet-observer` action to stop just
that observer; do not run a full Hotel service migration to quiet one monitor.
The daily balance invocation waits up to 60 seconds for the aggregate lock;
continued contention is a failed invocation, never a successful skipped check.

Use offline fixtures for exhaustion, concurrency, unknown outcomes and failed
notification tests. Never create business records or send staff test messages
just to validate monitoring. Keep routine model and Vercel probes at zero.

## Remaining separate work

This release does not implement app-side health endpoints, Registro's server
limit, Hotel's daily-summary delivery checkpoint, provider/business request
budgets, or new owner alert delivery. Those changes require their own workflow
tests and release. No six-priority-program completion claim follows from a
healthy process or this monitoring release.
