# Working In This Repo

This repo manages Owl's Watch OpenClaw agents.

Before designing a new agent or changing an existing one, read `docs/agent-design-guidelines.md`.

## Git And Deployment Discipline

- Use one worktree and one feature branch per Codex task. Never let two Codex
  instances edit the same worktree or branch.
- Branch from `main`, and open pull requests against `main`. Do not stack
  ordinary work on another feature branch.
- A completed change is not done because it was pushed to a feature branch or
  copied into a live workspace. It is done only after tests pass, the PR is
  merged, `main` is pushed, and the live deployment is made from that exact
  `origin/main` commit.
- Never deploy a dirty worktree, an unmerged branch, or a local `main` that does
  not exactly match `origin/main`. Deployment scripts enforce this rule.
- If work must stop before it is ready, commit it to a clearly named `wip/...`
  branch and push it. Never leave important work only as local modifications.
- After deployment, verify the gateway and channel, record the deployed commit,
  then remove the completed worktree and branch.

## Hard Rules

- Do not commit secrets, tokens, credentials, service-account JSON, auth state, sessions, memory logs, receipt spools, raw Gmail content, or generated quote sheets.
- Do not broaden an agent's tool access without updating `docs/security-boundaries.md`.
- Keep `main` as the OpenClaw conductor/default agent only. Do not give it business side-effect tools.
- Put business workflows in specialist agents with isolated workspaces, agentDirs, sessions, skills, and tool policies.
- Do not give Cuenta approval, modification, or deletion authority over expenses.
- Do not give Cotiza email-sending, booking, availability, or final quote status authority.
- Do not give Correo final email-send authority or Gmail mutation tools beyond explicitly enabled draft creation.
- Do not give Cobros final email-send authority. Cobros may create Gmail drafts with cuenta de cobro PDF attachments, but never sends them.
- Do not give Hotel broad PMS write authority or guest-message sending authority. Its only reservation write is the PMS-signed prepare/confirm create flow.
- Do not give Finca access outside the Operations finca-task subsystem.
- Operations app changes belong in the Operations repo, not here.

## Before Committing

Run:

```sh
./scripts/check-no-secrets.sh
./scripts/smoke-cuenta.sh
./scripts/smoke-cotiza.sh
./scripts/smoke-correo.sh
./scripts/smoke-cobros.sh
./scripts/smoke-hotel.sh
./scripts/smoke-finca.sh
openclaw --profile owlswatch config validate
openclaw --profile owlswatch agents list --bindings
```

Record which agent changed, which tools changed, whether credentials changed, and the smoke-test result.

Before deploying, also run `./scripts/assert-release-ready.sh`. It must confirm
that the checkout is clean, on `main`, and exactly synchronized with
`origin/main`.
