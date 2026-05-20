# Telegram Routing

Preferred Telegram setup is two separate private spaces behind the same OpenClaw gateway pattern.

## Owl's Watch Ops

The Owl's Watch Ops group is for Owl's Watch operational work:

- General topic -> main (`agentId: main`)
- Receipts topic -> Cuenta (`agentId: cuenta`)
- Quotes topic -> Cotiza (`agentId: cotiza`)
- Email topic -> Correo (`agentId: correo`)

Users should not need `/cotiza` or `/receipt` inside the correct topic.

## Dennis Brain

The Dennis Brain group is the general command-center capture space. It is broader than Owl's Watch and should route to Brain Intake (`agentId: brain`) through a top-level route binding:

```json
{
  "type": "route",
  "agentId": "brain",
  "match": {
    "channel": "telegram",
    "peer": {
      "kind": "group",
      "id": "<dennis_brain_group_id>"
    }
  }
}
```

Brain owns meaning, classification, project state, and receipts. OpenClaw owns Telegram runtime and routing.

The bot token and numeric chat IDs are runtime-only config values. Keep them out of git.

After routing changes:

```sh
openclaw --profile owlswatch config validate
openclaw --profile owlswatch channels status --probe
openclaw --profile owlswatch gateway restart
```
