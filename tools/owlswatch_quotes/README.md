# Owl's Watch Quote Tools

This plugin exposes narrow tools for Cotiza.

Tools read tokens and credentials from environment variables or `~/.openclaw-owlswatch/openclaw.json`. Tokens are never accepted as tool parameters and never returned in structured output.

Production mode calls the configured Operations APIs and Google APIs. Mock mode is available only when `OWLSWATCH_QUOTES_MOCKS=1` and is meant for local contract tests while Operations quote endpoints or Google credentials are not ready.

The plugin resolves its server/workspace from its own installation directory.
Each Python call has a deadline: 45 seconds for reads/preparation, 120 seconds
for create/revise/update and memory writes. Cancellation, timeout, oversized
output (256 KiB), process failures and invalid responses produce sanitized
errors. Stderr is drained without exposing it to the model. On macOS/Linux,
interrupted calls terminate their process group, escalating from SIGTERM to
SIGKILL after 250 ms. A failed write reports an **unconfirmed outcome** and is
not automatically retryable.

Cotiza's shared Telegram notification tool accepts only the group and topic
assigned to Cotiza in the trusted profile configuration. There must be exactly
one enabled route. The tool fills an omitted topic from that route and rejects
overrides; no recipient is inferred from quote/email text.

## Remaining Drive recovery limitation

Operations draft idempotency does not make Google Drive creation idempotent.
A Drive create/copy can succeed before its file ID is returned or attached to
the Operations row. After an unconfirmed write, reconcile the existing quote
and Drive files before retrying; do not generate a replacement idempotency key
or use `redo` to recover. Automatic recovery still requires a durable attempt
record and a stable Drive correlation marker. A negative Drive search after a
lost response is not proof that creation failed. This change bounds the waiting
process and exposes uncertainty; it does not close that remote side-effect gap.

`scripts/smoke-cotiza.sh` runs isolated offline quote and bridge fixtures,
including termination of a hung Python process and its child. No business APIs
or live workspaces are used by those tests.
