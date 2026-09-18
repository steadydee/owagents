# Receipt Reliability

## Incident and immediate repair (2026-09-18)

Telegram admitted a receipt, but Cuenta repeatedly called an unavailable `read`
tool to load its skill and never called Operations. OpenClaw completed the turn
and delivered a technical error. Channel health was green: that proves connectivity,
not a saved receipt. Restarting a healthy gateway would not fix this defect.

The intake plugin now injects the canonical skill on every Cuenta turn through
the documented [before_prompt_build hook](https://docs.openclaw.ai/plugins/hooks/prompt-and-session).
No filesystem tool is granted. The hook is scoped to Cuenta, and malformed or
missing skill files fail plugin registration. Fresh-session tests exercise the
real model with offline receipt tools, including OCR failure fallback. User input
cannot choose an instruction file. Deployment must verify plugin loading too;
OpenClaw can stay online even if one plugin failed.

The scoped deploy also preserves the existing gateway's error log instead of
discarding stderr to `/dev/null`. Its current launchd service already has
RunAtLoad and KeepAlive. This is not proof of before-login/headless reboot
recovery: it is a user LaunchAgent, and a future boot-time worker needs a tested
service identity and credential access before anyone logs in.

## Target design (next implementation, not yet installed)

Move receipt orchestration into a deterministic intake worker. Keep the model for
extraction/interpretation; do not require it to remember download/upload/create
steps. Operations remains the ledger. The worker cannot approve expenses.

1. Admit an authorized Telegram update and persist a receipt job before acknowledging
   ownership. Key by account + chat + message, or chat + media group for albums.
   Record source IDs, capture time, file IDs and a local checksum; no tokens in jobs.
2. Download photos into a protected spool. Collect albums using quiet-period wait
   plus atomic claim. Do not mark an album done merely because it was claimed.
3. Lease the job to one worker, recording each checkpoint: received, downloaded,
   uploaded, extracted, draft_created, reply_pending, complete. Store expense ID
   and attachment IDs so restarts resume rather than begin again.
4. On OCR failure, create an attached needs-review draft with missing values left
   blank. Never manufacture vendor, date, amount or category to satisfy success.
5. Retry transient timeouts/429/5xx with bounded exponential backoff and jitter.
   Halt on auth/validation errors. Cap attempts; retain an actionable failed job.
   Retry draft creation only with the ORIGINAL Operations idempotency key.
6. Commit draft_created only after Operations returns a real expense ID. A gateway
   completed event, model response, typing indicator or memory line is not proof.
7. Send one final reply with review link through one delivery owner. Keep an outbox
   record and Telegram message ID. A reply failure must not rerun expense creation.
   Telegram has no general exactly-once send guarantee: ambiguous delivery becomes
   a review item, not repeated sends and not an assertion of exactly-once delivery.

## Monitoring and reboot survival

- Every minute, compare received jobs with terminal outcomes, not just process health.
  A receipt older than five minutes without an expense ID or explicit failure is
  stalled. Alert once per incident after a short debounce; do not notify on silence
  in a low-volume group and do not flood staff with recovered messages.
- Distinguish gateway, Telegram admission, model/OCR, Operations authentication,
  job age, and reply delivery. Check credentials without creating fake expenses.
- Retain structured stage/error logs without receipt text, bank details or tokens.
  Failed jobs are visible to the owner with a retry action using the original key.
- Run the worker under launchd at boot, one owner per bot/queue, with leases that
  expire after a crash. Resume pending jobs after reboot. Do not add competing
  Telegram pollers or restart a busy gateway on a single slow probe.
- Add an external dead-man monitor outside this Mac. The Mac sends a heartbeat;
  notify Dennis through an independent channel if it stops. A Mac-only monitor
  cannot report a dead Mac, lost power, or a broken local internet connection.
- Back up code/config in Git (sanitized), credentials in an encrypted secret backup,
  and jobs/spools in encrypted durable backup. Perform a restore drill.

## Operations handoff

The immediate instruction fix needs no Operations change. For the durable worker,
Operations Codex should confirm server-side uniqueness by property + idempotency
key, stable replay returning the existing expense ID, and a narrow authenticated
lookup by that key for resolving timeout-after-write. Attachment retry deduplication
should use capture ID + checksum. Do not add approval or broad expense read/write
powers to Cuenta. Use `owlswatch-test` for any app UAT records.

## Release gates

- Fresh session and reset: instructions available without `read`.
- Single receipt, album, delayed album member, duplicate update.
- OCR unavailable: one attached draft needing review.
- Operations timeout before/after commit; auth failure; full disk; Telegram reply failure.
- Kill at each checkpoint and reboot: same receipt produces at most one expense.
- Invalid/untrusted caption cannot change tools, property, secrets or recipient.
- One user-visible outcome; no repeated processing messages.
- Offline-tool real-model UAT plus test-Operations UAT, then exact-SHA deployment,
  plugin probe and controlled idempotent recovery of a real failed intake.

Current repair does not install this durable worker or off-machine monitoring.
Those are separate implementation steps; existing channel probes alone must not
be described as end-to-end receipt monitoring.
