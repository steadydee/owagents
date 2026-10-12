# owlswatch_intake MCP Server

Narrow tool server for Cuenta receipt intake. The live profile exposes it through
the native OpenClaw plugin; the CLI/MCP interface remains available for tests.

The plugin's `before_prompt_build` hook includes the complete versioned skill
for Cuenta on every turn. This removes the hidden dependency on the unavailable
generic `read` tool. Other agents receive no receipt instructions. Skill loading
fails on an incomplete deployment; permissions are not broadened.

Sender verification uses trusted current-run Telegram identity and configured
numeric sender IDs, never a display name or a previous sender's refusal. The
Receipts topic must also enforce its own sender allowlist before dispatch.
Use `scripts/configure-cuenta-intake.mjs --config /absolute/openclaw.json` to
inspect the repair plan; add `--apply` to restrict only that topic to existing
approved IDs and set its existing session to `followup`. This processes queued
receipts individually instead of steering them into another receipt's run.
Normal session resets preserve this setting; reapply after deleting/recreating
the session. Reload the existing gateway safely after a deployment/configuration
change; do not install another gateway or Telegram poller.

Downloads accept only the configured profile's inbound media directory and the
configured Cuenta workspace's inbound staging directory. Resolved paths must
remain inside those roots; arbitrary workspace files and escaping symlinks are
rejected.

The server owns all external side effects:
- Telegram Bot API `getFile`, file download, and typing indicators. Cuenta has
  no direct-send grant; OpenClaw owns its single final reply.
- Durable album buffering and spool paths.
- Operations attachment upload and expense draft creation.
- Vision receipt extraction through a configured provider.
- Cuenta memory append.

Tokens are read from environment variables first, then from the owlswatch OpenClaw profile config. Tokens are never accepted as tool parameters and are never returned in results.

## Config Inputs

Preferred environment/config names:

- `TELEGRAM_BOT_TOKEN`
- `EXPENSE_INTAKE_API_TOKEN`
- `OPERATIONS_API_BASE_URL`
- `OWLSWATCH_VISION_API_KEY`
- `OWLSWATCH_VISION_ENDPOINT`
- `OWLSWATCH_VISION_MODEL`

OpenClaw passes these through `mcp.servers.owlswatch_intake.env` or `env.vars`.

## Verification

- `scripts/smoke-cuenta.sh`: sender/context, configuration-plan and media-path
  regressions plus the tool catalog.
- `scripts/verify-cuenta-telegram-gate.mjs /installed/openclaw`: optional offline
  check of the installed runtime's actual topic admission and queue resolution.
- `scripts/uat-cuenta-bootstrap.py`: fresh-session DeepSeek/OpenClaw UAT using
  offline tools (requires a runtime `DEEPSEEK_API_KEY`, never a CLI argument).
- `scripts/deploy-cuenta.sh`: deploy only Cuenta from clean synchronized main,
  preserving other agents and runtime state.

See `docs/receipt-reliability-design.md` for the durable worker and monitoring
design; those longer-term components are not installed by this instruction fix.
