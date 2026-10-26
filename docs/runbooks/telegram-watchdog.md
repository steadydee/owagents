# Telegram Recovery

OpenClaw owns Telegram long-poll recovery and channel-health restarts. Each
gateway also runs under a macOS LaunchAgent with `KeepAlive` and `RunAtLoad`, so
launchd restores a process that exits. Do not install a second restart loop.

Hotel also has a read-only observer installed as
`ai.openclaw.hotel.telegram-observer`. It runs every two minutes, calls only
OpenClaw's channel-status probe, journals accepted-message metadata without
message bodies, and sends transition-only Telegram alerts after an outage has
remained unhealthy for at least ten minutes. Brief network interruptions
recover silently. A recovery message is sent only if the matching outage alert
was successfully delivered and three consecutive checks are healthy. A failed
check or a gap longer than five minutes resets that count. The intermediate
state is `recovering`; a single good probe never closes an alerted incident.
It never calls
Telegram `getUpdates` and never restarts the gateway. OpenClaw remains the only
poller and recovery owner.

Transport restoration and task results are separate evidence. Notifications
say when Telegram is restored, when a newer successful agent run is observed,
or when an operator closes an incident. None of these messages asserts that
previously pending business work completed. Verify that outcome in the system
of record. In particular, a successful model run is not a receipt for a prior
reservation, government submission, email, or payment.

The runtime monitor keeps failed and unfinished runs unresolved after 24 hours.
The 24-hour window applies only to usage totals; older evidence is marked
`staleEvidence`. Content-free last-run evidence is retained in
`monitor/telegram-health-state.json` across telemetry rotation. A newer
successful run can clear an agent's failure indicator; a new start or an old
successful run cannot. A missing runtime-health module blocks recovery rather
than falling back to a channel-only healthy verdict.

An operator can close an incident after reconciling its result outside the
agent. This is a monitoring disposition, not permission to repeat any business
operation. Write `health/run-dispositions.json` inside the affected profile,
with mode `0600`, owned by the service user (or root), and no symlink. Copy the
exact `agent` and `evidenceId` from the runtime health output:

```json
{
  "schemaVersion": 1,
  "dispositions": [
    {
      "agent": "cotiza",
      "evidenceId": "<exact evidenceId from the incident>",
      "resolution": "closed_without_retry",
      "recordedAt": "<current UTC timestamp in ISO 8601 format>"
    }
  ]
}
```

Use `resolved_externally` when the result was verified and resolved in the
system of record, or `closed_without_retry` when the operator deliberately
closes it without retrying. Preserve previous entries when adding a disposition.
The timestamp must be at or after the incident and cannot be in the future
(apart from a 60-second clock allowance). A disposition matches only that
incident; it cannot suppress a later failure. The original run remains failed
in telemetry and usage totals, with `evidenceState: operator_disposition`.
Invalid or insecure disposition files do not clear incidents. Removing the
matching entry reopens retained failure evidence. Never grant an agent a tool
to edit this file.

Check the live profiles with:

```sh
openclaw --profile owlswatch channels status --probe
openclaw --profile hotel channels status --probe
openclaw --profile finca channels status --probe
```

OpenClaw durably spools accepted Telegram updates under:

```sh
~/.openclaw-owlswatch/telegram/ingress-spool-default
~/.openclaw-hotel/telegram/ingress-spool-default
~/.openclaw-finca/telegram/ingress-spool-default
```

Hotel monitoring records are stored locally under:

```sh
~/.openclaw-hotel/logs/gateway.error.log
~/.openclaw-hotel/logs/telegram-health.jsonl
~/.openclaw-hotel/logs/telegram-ingress-journal.jsonl
```

The ingress journal contains only message ID, chat ID, sender ID, and timestamp.
It intentionally excludes message text and media.

Install or refresh the observer after gateway service regeneration:

```sh
./scripts/install-hotel-telegram-observer.sh install
```

Do not restart a gateway merely because a spooled update is old. A startup
handler can leave an update retryable; restarting during that replay can
advance the offset without delivering a response.

Remove the retired external watchdog LaunchAgents:

```sh
./scripts/remove-external-telegram-watchdogs.sh
```

Diagnose a real failure with OpenClaw's own probes and logs:

```sh
openclaw --profile finca status --deep
openclaw --profile finca channels status --probe
openclaw --profile finca logs --follow
```

If OpenClaw reports false polling stalls during otherwise healthy long-running
work, upgrade OpenClaw first, then tune `channels.telegram.pollingStallThresholdMs`
within OpenClaw rather than adding a second watchdog. Export diagnostics before
manual restarts when the failure is repeatable. See the official [Telegram channel guide](https://docs.openclaw.ai/channels/telegram)
and [health checks guide](https://docs.openclaw.ai/health).

OpenClaw's standard macOS installer creates user LaunchAgents, which start only
after the `agent` account logs in. OpenClaw's own headless guidance recommends a
custom LaunchDaemon when the gateway must run before login. Install the Hotel
gateway, observer, daily summary, and Registro pickup as boot-level services on
the dedicated Mac mini with:

```sh
sudo ./scripts/install-headless-hotel-services.sh install
./scripts/install-headless-hotel-services.sh status
```

The installer runs every process as the unprivileged `agent` account. It
disables the duplicate user LaunchAgents while the system services are active.
Uninstalling restores the user LaunchAgents:

```sh
sudo ./scripts/install-headless-hotel-services.sh uninstall
```

Automatic login remains a useful fallback and can be checked with:

```sh
sysadminctl -autologin status
```

FileVault must remain off for automatic login. The Mac should also have
`autorestart 1` in `pmset -g custom` so it powers back on after an outage.
After changing the `agent` account password, turn automatic login off and back
on in macOS Login Options so `/etc/kcpassword` is refreshed; the status command
can still name the user when the stored password is stale.
