# Cobros Tools

Use only configured narrow `owlswatch_cobros_*` tools.

Allowed responsibilities:

- search targeted Gmail threads read-only
- read one Gmail thread
- prepare cuenta de cobro fields and warnings
- create Google Doc and exported PDF packets
- create Gmail drafts with PDF attached
- send short Telegram notifications
- log memory

Forbidden:

- sending final email
- deleting, archiving, labeling, or mutating Gmail messages
- broad filesystem/browser/web/shell access
- direct database access
- token handling in prompts or replies

## Current Contract

- Gmail read returns a `sourceId` and bounded preview; prepare takes that `sourceId`.
- Pasted requests use prepare `raw_text`; model authority/correction overrides are rejected.
- Packet and Gmail draft tools take only `preparedId`. They load trusted fields,
  recipient and PDF from the durable journal, and replay completed steps safely.
- Gmail recipients are source-bound or configured for manual requests. Telegram
  chat/topic are configured and cannot be redirected by tool arguments.
- Unknown write outcomes require reconciliation, never a replacement ID or blind retry.
- The Cobros runtime hook permits six searches, twelve reads, and two uses of an
  identical read query per run. Stop at the budget and request a narrower source.
