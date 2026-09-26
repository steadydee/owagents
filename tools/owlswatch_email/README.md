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
messages are excluded. Records lacking a thread ID require human cleanup.

State is written atomically under `tasks/email_runtime/`: `checkpoint.json`,
`active.json`, `processed.json`, per-scan `runs/`, `schedule-status.json`, and
`schedule_runs/`. The scan and scheduler use process locks. Gmail drafts use an
operation journal keyed by mailbox/thread/source-message, with a deterministic
RFC Message-ID. Repeated requests reuse the confirmed draft; ambiguous outcomes
search Gmail for that operation and fail closed if no match is confirmed.
Never delete an unknown operation to force retry; inspect Gmail first. Existing
human edits to a confirmed draft are preserved.

Open tasks include all waiting states. New records get `owner` from
`OWLSWATCH_EMAIL_REVIEW_OWNER`, otherwise `unassigned`; default next action is
2 hours for urgent, 8 for high, and 24 for normal/low. A caller can explicitly
supply a valid `nextActionAt`. Scheduled daily/follow-up summaries are generated
deterministically from local metadata, identify unresolved/unassigned work, and
remain separate from new-message processing. They do not claim stale local
records prove a thread is still unanswered.

Telegram destination and optional topic are fixed by runtime configuration.
Destination arguments are rejected. Gmail draft recipients must match the
current external message's Reply-To or sender; the sender mailbox is fixed.

The plugin reads its schemas from the Python catalog rather than maintaining a
second schema table. Install the pinned `requirements.txt` using the configured
runtime interpreter (`OWLSWATCH_EMAIL_PYTHON`), and run `scripts/smoke-correo.sh`.
`install-email-schedules.sh` copies launch wrappers into the fixed profile `bin`
directory; plists never depend on a temporary source worktree. Logs live under
the profile `logs` directory. The existing enable file remains the kill switch.
