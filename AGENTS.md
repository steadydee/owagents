# Working In This Repo

This repo manages Owl's Watch OpenClaw agents.

Before designing a new agent or changing an existing one, read `docs/agent-design-guidelines.md`.

## Hard Rules

- Do not commit secrets, tokens, credentials, service-account JSON, auth state, sessions, memory logs, receipt spools, raw Gmail content, or generated quote sheets.
- Do not broaden an agent's tool access without updating `docs/security-boundaries.md`.
- Keep `main` as the OpenClaw conductor/default agent only. Do not give it business side-effect tools.
- Put business workflows in specialist agents with isolated workspaces, agentDirs, sessions, skills, and tool policies.
- Do not give Cuenta approval, modification, or deletion authority over expenses.
- Do not give Cotiza email-sending, booking, availability, or final quote status authority.
- Do not give Correo final email-send authority or Gmail mutation tools beyond explicitly enabled draft creation.
- Do not give Cobros final email-send authority. Cobros may create Gmail drafts with cuenta de cobro PDF attachments, but never sends them.
- Operations app changes belong in the Operations repo, not here.

## Before Committing

Run:

```sh
./scripts/check-no-secrets.sh
./scripts/smoke-cuenta.sh
./scripts/smoke-cotiza.sh
./scripts/smoke-correo.sh
./scripts/smoke-cobros.sh
openclaw --profile owlswatch config validate
openclaw --profile owlswatch agents list --bindings
```

Record which agent changed, which tools changed, whether credentials changed, and the smoke-test result.
