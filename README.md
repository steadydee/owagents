# Owl's Watch Agents

Source repo for the Owl's Watch OpenClaw agent layer.

This repo is source and deployment scaffolding. It is not a copy of the live `~/.openclaw-*` runtime state.

## Agents

- `main` / `Owl's Watch Ops`: default conductor. Routes people to the right specialist and has no business side-effect tools.
- `Cuenta`: Telegram receipt intake clerk. Creates expense drafts only.
- `Cotiza`: quote drafting clerk. Reads quote requests, creates Operations draft rows, and creates Google Drive quote sheets.
- `Correo`: operational email drafting clerk. Reads Gmail, uses Luna context, creates Email Desk draft tasks, and never sends final email.
- `Cobros`: cuenta de cobro drafting clerk. Reads accounting requests, creates Drive Doc/PDF packets and Gmail drafts with attached PDFs, and never sends final email.

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
- `docs/`: architecture, security, Telegram routing, and runbooks.

## Normal Deploy

```sh
cd /Users/agent/code/owlswatch/owlswatch-agents
./scripts/check-no-secrets.sh
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
