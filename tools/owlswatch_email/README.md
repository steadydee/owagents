# Owl's Watch Email Tools

This plugin exposes narrow tools for Correo, the Owl's Watch email drafting agent.

The tools are intentionally scoped:

- Gmail tools are read-only unless Gmail draft creation is explicitly enabled.
- Luna is called only through `get_email_response_context` with a scoped machine token.
- Local task files are used for de-duplication and recovery.
- Telegram is used only for operational notifications.
- No tool sends final email.

Required runtime values live in `~/.openclaw-owlswatch/openclaw.json` under `mcp.servers.owlswatch_email.env` or in the process environment.

Required for read-only scan:

- `GOOGLE_APPLICATION_CREDENTIALS`
- `OWLSWATCH_GMAIL_ACCOUNT`

Required for Luna context:

- `OW_AGENT_TOKEN_SECRET`
- `LUNA_BASE_URL`

Optional:

- `OWLSWATCH_EMAIL_NOTIFY_CHAT_ID`
- `OWLSWATCH_EMAIL_NOTIFY_THREAD_ID`
- `OWLSWATCH_GMAIL_DRAFTS_ENABLED=1`

Gmail draft creation also requires the Workspace domain-wide delegation scope:

`https://www.googleapis.com/auth/gmail.compose`

Correo creates Gmail drafts only. Humans review, edit, and send in Gmail.

## Incremental schedules and recovery

`run.py` is the deterministic scheduler entry point. It reads Gmail history and
starts Correo only for changed external messages, with at most four candidates
per model turn (normally under 32 tools). Provider discovery pages are capped at
20 records and persisted before body retrieval. The remainder is processed on
subsequent runs; the watermark is not moved past unacknowledged work. Bootstrap
covers seven days; expired history recovers from the last successful checkpoint
with a one-day overlap (30 days when no timestamp is available), using bounded
pages. Pending items are refreshed if Gmail changes during failure recovery.

The runner treats only `owlswatch_email_acknowledge_item` receipts as progress.
Actionable receipts require a saved exact-source task, a confirmed draft for
`draft_ready`, and a successful configured Telegram handoff record. Ignored
classification is an explicit per-message receipt. A process exit code or a
model's final text cannot clear pending work. Classification itself remains a
model judgment; this is not a claim that an acknowledged classification is
factually correct. A small rotating Gmail sweep reconciles old local records
only when the latest meaningful message is a confirmed SENT reply. Draft
messages are excluded. An explicit source message must be present before the
confirmed reply in that thread; source-less legacy records stay open even when
that thread contains sent mail. Explicit waits for quotes, payment, availability
or information also stay open because a reply does not prove the business
dependency completed. These records expose the verified sent message separately
from their unresolved handoff state.

State is written atomically under `tasks/email_runtime/`: `checkpoint.json`,
`active.json`, `processed.json`, per-scan `runs/`, `schedule-status.json`, and
`schedule_runs/`. The scan and scheduler use process locks. Gmail drafts use an
operation journal keyed by mailbox/thread/source-message, with a deterministic
RFC Message-ID. Repeated requests reuse the confirmed draft; ambiguous outcomes
search Gmail for that operation and fail closed if no match is confirmed.
Never delete an unknown operation to force retry; inspect Gmail first. Existing
human edits to a confirmed draft are preserved.

Open tasks include all waiting states. The operator sets the default reviewer
in runtime `review-policy.json` (fallback: `OWLSWATCH_EMAIL_REVIEW_OWNER`, then
`unassigned`). Existing assigned owners are preserved. Due times are 2 hours
for urgent, 8 for high, and 48 for normal/low; the normal deadline is configurable
from 1–168 hours. Model payloads cannot assign reviewers, postpone deadlines or
forge reconciliation proof. A new source message starts a new deadline; repeated
updates to the same work do not reset it. Daily/follow-up summaries show owners,
overdue records, missing source linkage and distinct threads whose most recent
Gmail check found a pending external reply. Stale local records are never
presented as counts of unanswered email.

The operator-only `review_backlog.py` command reads at most 30 Gmail threads per
invocation by default, writes a private content-free evidence inventory, and
never sends messages or mutates Gmail. Literal legacy provider IDs and explicit
task references are candidates until Gmail confirms their identity. Generated
task hashes and historical notes are not evidence. `--apply` backfills owner and
deadline metadata and closes only tasks with a fresh confirmed SENT reply;
missing sources, provider errors and unverified outcomes remain visible. Apply
requires an inspected plan with the same policy, skips any task changed since
that plan, refreshes Gmail immediately before every potential closure, and
persists complete rollback records before writing local state. It retains a
before/after change journal. For example, on the deployed tool package:

```sh
python3 review_backlog.py --read-gmail --limit 30 --owner '<reviewer>'
# Repeat bounded reads until remainingThreadChecks is zero; inspect inventory.
python3 review_backlog.py --apply --owner '<reviewer>' --sla-hours 48
```

Evidence older than 24 hours cannot close tasks. Runtime inventories and policy
files contain private business context and must never enter source control.

Telegram destination and optional topic are fixed by runtime configuration.
Destination arguments are rejected. Gmail draft recipients must match the
current external message's Reply-To or sender; the sender mailbox is fixed.

The plugin reads its schemas from the Python catalog rather than maintaining a
second schema table. Install the pinned `requirements.txt` using the configured
runtime interpreter (`OWLSWATCH_EMAIL_PYTHON`), and run `scripts/smoke-correo.sh`.
`install-email-schedules.sh` copies launch wrappers into the fixed profile `bin`
directory; plists never depend on a temporary source worktree. Logs live under
the profile `logs` directory. The existing enable file remains the kill switch.
