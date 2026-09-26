# Runtime limits and recovery

Apply source and configuration only from clean `main` matching `origin/main`.
The release scripts run `scripts/smoke-platform.sh` before copying source and
record source hashes in each profile's `source-release.json`. They preserve
runtime records, approvals, submission journals, Gmail cursors, credentials,
memory and user files. Source backups are local rollback aids, not a tested
off-site disaster-recovery service.

After source deployment, apply the runtime policy:

```sh
python3 scripts/harden-runtime.py --profile owlswatch --apply
python3 scripts/harden-runtime.py --profile hotel --apply
python3 scripts/harden-runtime.py --profile finca --apply
python3 scripts/harden-runtime.py --profile bailey-finance --apply
```

This disables paid idle heartbeats, enables OpenClaw loop detection, sets a
600-second default run timeout, installs the native guard, allows Correo's exact
item acknowledgement, and disables redundant MCP transports while preserving
their configuration. Models, credentials and conversation routes are preserved.
Luna is outside this rollout.

The guard limits a run to 40 tool attempts, 12 searches, three identical reads,
and ten minutes before starting another tool. The installed runtime reports
aggregate tokens after an attempt; token telemetry is not an in-loop token cap.
Cobros additionally limits Gmail work to six searches and twelve reads. These
are ceilings, not targets. A blocked run must report unfinished work and resume
from durable state. Limits cannot cancel a provider call already in progress;
OpenClaw's timeout remains necessary.

An observed provider credit failure starts a durable 30-minute cooldown for that
profile/provider. Other known providers remain usable. This is not a monetary
spending cap or a provider balance query. After replenishing credit, allow the
cooldown to expire and retry the pending request. Never mark pending work done
because a process exited successfully.

Inspect content-free health and usage:

```sh
python3 scripts/agent-health.py --profile owlswatch
openclaw --profile owlswatch channels status --probe
```

`health/runs.jsonl` records agent, provider, model, token counts, duration, tool
count and classified outcomes; it contains no prompt or response text. Logs
rotate at 5 MB with one retained file. A channel probe alone does not prove an
agent can complete work. The observer combines it with the latest agent result
and provider cooldown. It sends transition alerts through its existing configured
staff route; it never polls Telegram updates or restarts a gateway.

Refresh observers with `OPENCLAW_PROFILE=<profile>
scripts/install-hotel-telegram-observer.sh install`. Keep one supervisor per
profile. Hotel uses system LaunchDaemons and its duplicate GUI jobs remain
disabled; the other profiles use their existing GUI owner. Do not introduce a
second watchdog. Preserve stderr. Refresh Hotel system plists using
`sudo scripts/install-headless-hotel-services.sh install` after refreshing its
source LaunchAgent plists.

Correo's pinned dependencies are in `tools/owlswatch_email/requirements.txt`.
Install them with the same Python used by the gateway, then refresh
`scripts/install-email-schedules.sh`. Fixed profile `bin` wrappers survive removal
of a release worktree. Unchanged Gmail polls never start a model. Batches contain
at most four candidates, with exact-message acknowledgements and atomic cursors.
The daily summary and follow-up digest are deterministic. Waiting records stay
visible, and Gmail SENT evidence is required for automatic closure.

Hotel's Registro pickup invokes the fixed tool directly without a model.
Uncertain submission outcomes require operator reconciliation, not a fresh
submission. If the provider receipt is known but PMS recording failed, retry the
same operation to save that receipt only. Reservation confirmations use the
authenticated `/confirmar_reserva <reference>` command; old drafts without the
new provenance must be prepared again.

Before any manual restart, inspect for an active write and preserve diagnostic
metadata. Restart only the existing launchd owner, then verify configuration,
plugin loading, channel probe, and recorded release SHA. A green probe does not
replace an authorized end-to-end human UAT of business writes.

For rollback, stop new scheduled work, restore the release's source backup and
the `before-hardening-*.json` config backup recorded in `runtime-release.json`,
and restart the same owner. Preserve all durable state, especially unknown
external outcomes. Do not roll back across a journal contract change and resume
writes without reconciling those operations first.
