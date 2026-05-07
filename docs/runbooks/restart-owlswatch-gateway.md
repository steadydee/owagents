# Restart Owlswatch Gateway

Use this after OpenClaw config, tool schemas, plugin manifests, or skill instructions change.

```sh
openclaw --profile owlswatch config validate
openclaw --profile owlswatch gateway restart
openclaw --profile owlswatch gateway status
openclaw --profile owlswatch channels status --probe
```

Do not restart `frontier` unless the task explicitly requires it.

