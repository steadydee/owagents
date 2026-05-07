# Telegram Routing

Preferred Telegram setup is one private group with forum topics:

- Receipts topic -> Cuenta (`agentId: main`)
- Quotes topic -> Cotiza (`agentId: cotiza`)

Users should not need `/cotiza` or `/receipt` inside the correct topic.

The bot token and numeric chat IDs are runtime-only config values. Keep them out of git.

After routing changes:

```sh
openclaw --profile owlswatch config validate
openclaw --profile owlswatch channels status --probe
openclaw --profile owlswatch gateway restart
```

