# Mac Mini Deployment

Runtime profile:

- OpenClaw profile: `owlswatch`
- Profile config: `~/.openclaw-owlswatch/openclaw.json`
- Gateway port: `19001`
- Cuenta workspace: `~/.openclaw/workspace-owlswatch`
- Cotiza workspace: `~/.openclaw/workspace-owlswatch-cotiza`

Deploy source:

```sh
cd /Users/agent/code/owlswatch/owlswatch-agents
./scripts/check-no-secrets.sh
./scripts/deploy-to-mac-mini.sh
./scripts/smoke-cuenta.sh
./scripts/smoke-cotiza.sh
openclaw --profile owlswatch config validate
openclaw --profile owlswatch skills check --agent cotiza
openclaw --profile owlswatch gateway restart
```

Frontier/Lumen uses a different profile and gateway port. Do not restart it for Owl's Watch deploys.

