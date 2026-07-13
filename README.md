# Owl's Watch Agents

Source repo for the Owl's Watch OpenClaw agent layer.

This repo is source and deployment scaffolding. It is not a copy of the live `~/.openclaw-*` runtime state.

## Agents

- `main` / `Owl's Watch Ops`: default conductor. Routes people to the right specialist and has no business side-effect tools.
- `Cuenta`: Telegram receipt intake clerk. Creates expense drafts only.
- `Cotiza`: quote drafting clerk. Reads quote requests, creates Operations draft rows, and creates Google Drive quote sheets.
- `Correo`: operational email drafting clerk. Reads Gmail, uses Luna context, creates Email Desk draft tasks, and never sends final email.
- `Cobros`: cuenta de cobro drafting clerk. Reads accounting requests, creates Drive Doc/PDF packets and Gmail drafts with attached PDFs, and never sends final email.
- `Hotel`: PMS operations clerk. Reads reservation operations data and sends staff-only Telegram notifications from a separate `hotel` profile/bot.
- `Finca`: task-tracking clerk. Creates and updates Operations finca tasks from a separate private worker group and posts the 07:00 outstanding-task report.

## Boundaries

- Operations is the source of truth for expenses, quotes, and Email Desk tasks.
- Agents are clerks only.
- `main` is a conductor only; it should not hold receipt, quote, email, accounting-document, shell, browser, filesystem, gateway, cron, node, or web tools.
- Tokens stay inside narrow tools.
- Skills define workflow; tools perform side effects.
- No raw Gmail, receipt photos, quote exports, runtime memory logs, sessions, or credentials belong in git.

## Repo Layout

- `openclaw/agents/`: identity files and skills.
- `openclaw/profiles/`: sanitized OpenClaw config examples.
- `tools/`: MCP/plugin tool servers.
- `data/`: versioned business rules and schemas used by agents.
- `scripts/`: deploy, smoke-test, backup, and safety scripts.
- `docs/`: architecture, security, Telegram routing, and runbooks. Start with `docs/agent-design-guidelines.md` before agent work.

The Operations implementation handoff for Finca is `docs/finca-operations-contract.md`. Telegram/runtime activation is documented in `docs/runbooks/setup-finca.md`.

## Normal Deploy

Live deployments are allowed only from a clean `main` checkout whose `HEAD`
exactly matches a freshly fetched `origin/main`. The deployment scripts enforce
this through `scripts/assert-release-ready.sh` and run the secret scan before
copying files.

```sh
cd /Users/agent/code/owlswatch/owlswatch-agents
./scripts/assert-release-ready.sh
./scripts/deploy-to-mac-mini.sh
openclaw --profile owlswatch config validate
openclaw --profile owlswatch skills check --agent main
openclaw --profile owlswatch skills check --agent cuenta
openclaw --profile owlswatch skills check --agent cotiza
openclaw --profile owlswatch skills check --agent correo
openclaw --profile owlswatch skills check --agent cobros
openclaw --profile owlswatch gateway restart
```

The deploy script copies source files into the live OpenClaw workspaces and leaves runtime state untouched.

Hotel/PMS operations uses a separate profile:

```sh
./scripts/deploy-hotel-to-mac-mini.sh
./scripts/smoke-hotel.sh
openclaw --profile hotel config validate
openclaw --profile hotel skills check --agent hotel
```

Finca worker tasks use a separate profile and bot:

```sh
./scripts/deploy-finca-to-mac-mini.sh
./scripts/smoke-finca.sh
openclaw --profile finca config validate
openclaw --profile finca skills check --agent finca
```
