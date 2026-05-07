# Working In This Repo

This repo manages Owl's Watch OpenClaw agents.

## Hard Rules

- Do not commit secrets, tokens, credentials, service-account JSON, auth state, sessions, memory logs, receipt spools, raw Gmail content, or generated quote sheets.
- Do not broaden an agent's tool access without updating `docs/security-boundaries.md`.
- Do not give Cuenta approval, modification, or deletion authority over expenses.
- Do not give Cotiza email-sending, booking, availability, or final quote status authority.
- Operations app changes belong in the Operations repo, not here.

## Before Committing

Run:

```sh
./scripts/check-no-secrets.sh
./scripts/smoke-cuenta.sh
./scripts/smoke-cotiza.sh
```

Record which agent changed, which tools changed, whether credentials changed, and the smoke-test result.

