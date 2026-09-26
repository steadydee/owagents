# Cobros recovery, 2026-09-26

The gateway received the request. Cobros repeatedly searched for missing billing
data until its context overflowed; compaction exhausted retries and left the
topic mapped to an unusable session. A connected channel did not imply a working
business workflow. Increasing the compaction reserve alone is not a repair.

## Immediate safeguards

- Load the canonical skill from the trusted plugin; filesystem tools stay denied.
- Enforce three searches and three thread reads per trusted run, across prompt rebuilds.
- Limit search results to five and thread responses to 24K characters, without
  duplicated rawText. Prepare uses the full server-retained sourceId for dispute
  and duplicate validation, preserving the newly merged immutable preparations.
- Targeted billing-identity searches need not contain accounting vocabulary.
- Bound subprocess duration/output; uncertain write outcomes must be checked,
  never blindly retried. Missing data receives one concise question.
- Enable Cobros-only loop detection. The final inbound reply owns notification.

Deploy from exact clean origin/main with scripts/deploy-cobros.sh. Preserve
spool, secrets, session history, and existing poller ownership. Recover exhausted
topic sessions using the supported gateway sessions.reset API. This is not an
automatic end-to-end recovery system.

## Architecture direction

The concurrent platform-hardening release introduced durable source/preparation
and side-effect journals. This patch preserves those changes. The broader
end-to-end deadline/outbox architecture below still needs acceptance testing;
it is not implied by a channel health probe.

Use OpenClaw for conversation/intent, with a durable business job runner owning
search budgets, validation, artifact creation, retries and notification. Persist
received, processing, needs_info, ready, creating, completed, failed, and unknown
write outcome states independently of conversation history. Resume missing-field
requests by job ID; do not repeat the full Gmail research after each reply.

Journal each side effect and verify existing artifacts before retrying. Delivery
needs an outbox and idempotency independent of the model. A deadline must produce
a deterministic useful failure response even if the model fails or compaction
cannot recover. Monitor oldest unacknowledged request and terminal outcome,
not just gateway/channel health. Alert the owner, not every staff group, once per
incident. External monitoring must detect host/network loss.

Keep one Telegram ingress owner per bot. Use boot-surviving services and separate
workers/queues so email backlog cannot stall receipt or Cobros requests. Stage
one workflow at a time; do not rewrite every agent or change app boundaries in
an emergency deployment. Finca and Brain remain intentionally disabled.

Acceptance: restart during retrieval, lost Gmail reply, exhausted model context,
missing NIT, provider timeout, partial Doc/PDF creation, Telegram delivery failure,
and repeated source messages all end with an accurate outcome and no duplicate
accounting document or final email send.
