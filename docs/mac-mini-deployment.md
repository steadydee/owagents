# Mac Mini Deployment

Runtime profile:

- OpenClaw profile: `owlswatch`
- Profile config: `~/.openclaw-owlswatch/openclaw.json`
- Gateway port: `19001`
- Cuenta workspace: `~/.openclaw/workspace-owlswatch`
- Cotiza workspace: `~/.openclaw/workspace-owlswatch-cotiza`
- Correo workspace: `~/.openclaw/workspace-owlswatch-correo`
- Cobros workspace: `~/.openclaw/workspace-owlswatch-cobros`

Deploy source:

```sh
cd /Users/agent/code/owlswatch/owlswatch-agents
./scripts/check-no-secrets.sh
./scripts/deploy-to-mac-mini.sh
./scripts/smoke-cuenta.sh
./scripts/smoke-cotiza.sh
./scripts/smoke-correo.sh
./scripts/smoke-cobros.sh
openclaw --profile owlswatch config validate
openclaw --profile owlswatch skills check --agent main
openclaw --profile owlswatch skills check --agent cotiza
openclaw --profile owlswatch skills check --agent correo
openclaw --profile owlswatch skills check --agent cobros
openclaw --profile owlswatch gateway restart
```

Frontier/Lumen uses a different profile and gateway port. Do not restart it for Owl's Watch deploys.

## Finca Profile

- Profile: `finca`
- Config: `~/.openclaw-finca/openclaw.json`
- Workspace: `~/.openclaw/workspace-finca-ops`
- Gateway port: `19501`

```sh
./scripts/check-no-secrets.sh
./scripts/deploy-finca-to-mac-mini.sh
./scripts/smoke-finca.sh
openclaw --profile finca config validate
openclaw --profile finca skills check --agent finca
openclaw --profile finca gateway install
./scripts/install-finca-schedule.sh
```

Do not enable the daily schedule until the Operations Finca tools, private bot,
group id, user allowlist, and production app credential are verified.

## Telegram Recovery

Keep OpenClaw on the current stable release; Hotel requires at least
`2026.7.1-2` for the polling-liveness and durable-ingress fixes. Do not install
an external Telegram restart watchdog. OpenClaw owns long-poll recovery
and channel-health restarts, while each gateway LaunchAgent uses `KeepAlive` and
`RunAtLoad` for process recovery. External scripts that restart a gateway while
it is replaying a durable update can lose the reply.

Hotel has a separate read-only observer. It records channel health and accepted
message metadata, preserves gateway stderr, and alerts on failure/recovery. It
does not poll Telegram and does not restart OpenClaw.

```sh
./scripts/install-hotel-telegram-observer.sh install
```

Remove any legacy watchdogs after restoring an older Mac backup:

```sh
./scripts/remove-external-telegram-watchdogs.sh
openclaw --profile owlswatch channels status --probe
openclaw --profile hotel channels status --probe
openclaw --profile finca channels status --probe
```

See `docs/runbooks/telegram-watchdog.md`.

## Email Schedules

Correo email schedules are disabled unless installed explicitly after Operations Email Desk and the Luna machine token are configured.

Before enabling schedules, add an `Email` topic to the existing Owl's Watch Ops Telegram group and set its thread id in:

```json
"OWLSWATCH_EMAIL_NOTIFY_THREAD_ID": "<email-topic-thread-id>"
```

Use the existing `owbot` unless a separate bot is intentionally created later.

```sh
./scripts/install-email-schedules.sh
```

This installs:

- polling scan every 30 minutes
- daily important-email summary at 08:00
- unanswered-email scan at 08:15

Disable without removing plists:

```sh
./scripts/install-email-schedules.sh disable
```
