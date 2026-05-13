# Rotate Email Agent Token

The Email Desk agent token lets Correo submit draft tasks and scan summaries to Operations.

The token must stay in runtime-only storage. Do not commit it, print it, paste it into agent prompts, or add it to documentation.

Runtime location on the Mac mini:

```text
~/.openclaw-owlswatch/secrets/email-agent-token.tmp
```

Rotation steps:

1. Generate or retrieve the new Operations `EMAIL_AGENT_API_TOKEN`.
2. Update the Operations/Vercel environment first.
3. Update the Mac mini runtime token file:

```sh
install -m 600 /dev/null ~/.openclaw-owlswatch/secrets/email-agent-token.tmp
```

Then place the token into that file using a local secure editor or paste flow.

4. Restart only the Owl's Watch gateway:

```sh
openclaw --profile owlswatch gateway restart
```

5. Verify:

```sh
./scripts/smoke-correo.sh
openclaw --profile owlswatch channels status
```

6. Process one harmless test email and confirm Operations receives the Email Desk task.

If scheduled polling is enabled, confirm it still works after rotation. If testing manually, leave schedules disabled.
