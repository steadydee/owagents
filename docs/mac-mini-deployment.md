# Mac Mini Deployment

Runtime profile:

- OpenClaw profile: `owlswatch`
- Profile config: `~/.openclaw-owlswatch/openclaw.json`
- Gateway port: `19001`
- Cuenta workspace: `~/.openclaw/workspace-owlswatch`
- Cotiza workspace: `~/.openclaw/workspace-owlswatch-cotiza`
- Correo workspace: `~/.openclaw/workspace-owlswatch-correo`

Deploy source:

```sh
cd /Users/agent/code/owlswatch/owlswatch-agents
./scripts/check-no-secrets.sh
./scripts/deploy-to-mac-mini.sh
./scripts/smoke-cuenta.sh
./scripts/smoke-cotiza.sh
./scripts/smoke-correo.sh
openclaw --profile owlswatch config validate
openclaw --profile owlswatch skills check --agent main
openclaw --profile owlswatch skills check --agent cotiza
openclaw --profile owlswatch skills check --agent correo
openclaw --profile owlswatch gateway restart
```

Frontier/Lumen uses a different profile and gateway port. Do not restart it for Owl's Watch deploys.

## Telegram Watchdog

Install the watchdog so Telegram polling is restarted if it stops inside an otherwise healthy gateway:

```sh
./scripts/install-telegram-watchdog.sh
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
