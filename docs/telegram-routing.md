# Telegram Routing

Preferred Telegram setup for this profile is one private Owl's Watch Ops group with forum topics routed directly to specialist agents.

## Owl's Watch Ops

The Owl's Watch Ops group is for Owl's Watch operational work:

- General topic -> main (`agentId: main`)
- Receipts topic -> Cuenta (`agentId: cuenta`)
- Quotes topic -> Cotiza (`agentId: cotiza`)
- Email topic -> Correo (`agentId: correo`)
- Cuentas de Cobro topic -> Cobros (`agentId: cobros`)
- Registro topic -> Registro (`agentId: registro`)

Users should not need `/cotiza`, `/receipt`, `/cobros`, or `/registro` inside the correct topic.

## OW Finca

Finca tasks use a separate private Telegram group and bot under the `finca`
OpenClaw profile. The group is not a topic inside Owl's Watch Ops.

- Group name: `OW Finca`
- Agent: `finca`
- Mentions/slash commands: not required
- Telegram privacy mode: disabled through BotFather
- Authorization: every worker's numeric user ID must be present in the profile
  allowlist; group membership alone is insufficient

## Personal Brain

The Dennis Brain/private dashboard project is not part of the `owlswatch` profile. Give it its own profile, bot, and routing so Owl's Watch operational messages cannot wake up the wrong agent.

The bot token and numeric chat IDs are runtime-only config values. Keep them out of git.

After routing changes:

```sh
openclaw --profile owlswatch config validate
openclaw --profile owlswatch channels status --probe
openclaw --profile owlswatch gateway restart
```
