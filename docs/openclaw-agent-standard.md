# OpenClaw Agent Standard

This repo follows OpenClaw's multi-agent model:

- One `agentId` is one isolated brain: workspace, `agentDir`, auth profiles, model registry, sessions, skills, and tool policy.
- `main` is the default/fallback agent. For Owl's Watch, `main` is only the conductor.
- Specialist work lives in specialist agents: `cuenta`, `cotiza`, `correo`, and `cobros`.
- Telegram forum topics route directly to specialists with `channels.telegram.groups.<chatId>.topics.<threadId>.agentId`.
- Per-agent tool policies enforce authority boundaries. Deny broad shell, browser, filesystem, gateway, cron, node, and web tools unless a future review explicitly approves them.
- Per-agent skill allowlists are explicit. A non-empty list is final for that agent; `[]` means no visible skills.

## Owl's Watch Routing

- General topic -> `main`
- Receipts topic -> `cuenta`
- Quotes topic -> `cotiza`
- Email topic -> `correo`
- Cuentas de Cobro topic -> `cobros`

Hotel/PMS operations uses a separate `hotel` profile and bot. It is not bound
into OWBot because PMS operations has a distinct notification surface and should
not share the receipts/quotes/cobros Telegram routing.

Finca worker tasks use a separate `finca` profile and bot. Workers interact in
the private `OW Finca` group, while Operations remains the task source of truth.

The conductor may explain where work belongs, but it does not create drafts or call business tools.

## Non-Owl's Watch Agents

Personal Brain or dashboard agents should use their own profile, bot, and routing. Do not bind non-Owl's Watch Telegram groups into the `owlswatch` profile.

## Standard Workspace Files

Each agent source folder should include:

- `AGENTS.md`: operating rules.
- `SOUL.md`: persona and boundaries.
- `USER.example.md`: safe user context template.
- `IDENTITY.md`: agent name and short identity.
- `TOOLS.md`: local tool notes; not access control.
- `MEMORY.template.md`: safe memory stub.
- `README.md`: purpose, boundaries, and setup.
- `skills/<skill-name>/SKILL.md` for specialist workflows.

Live `USER.md`, `MEMORY.md`, `memory/`, sessions, spools, auth state, and credentials stay out of git.

## Sources

- https://docs.openclaw.ai/concepts/multi-agent
- https://docs.openclaw.ai/agent-workspace
- https://docs.openclaw.ai/channels/telegram
- https://docs.openclaw.ai/tools/skills
- https://docs.openclaw.ai/tools
