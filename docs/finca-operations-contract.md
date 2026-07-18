# Operations Contract For OW Finca Tasks

Give this document to the Codex that owns the Operations app. The OpenClaw Finca agent is built against this contract.

## Product Boundary

Operations is the only task source of truth. Telegram and OpenClaw may create and update tasks only through the narrow interfaces below.

Do not add due dates in v1. Do not expose payroll, salary, expense, quote, reservation, or private employee fields through Finca tools.

## Data Model

Add a `FincaTaskStatus` enum:

```text
open
in_progress
blocked
completed
cancelled
```

Add `FincaWorker` with:

- UUID id
- propertyId
- displayName
- optional telegramUserId and telegramHandle
- optional employeeId link
- active
- createdAt and updatedAt
- unique propertyId + telegramUserId

Add `FincaTask` with:

- UUID id and global autoincrement sequence used to display `F-####`
- propertyId
- title and optional details
- optional estimatedMinutes integer from 1 through 10,080
- priority boolean, default false
- status, default open
- progressPercent integer, default 0, constrained 0 through 100
- optional blockedReason and assignedWorkerId
- source and Telegram chat/user/message ids
- createdBy actor type/id/name
- optional idempotencyKey, unique with propertyId
- optimistic-lock version integer
- startedAt, completedAt, cancelledAt, createdAt, updatedAt

Add `FincaTaskEvent` with:

- task id
- event type
- actor type/id/name
- optional note
- previousState and newState JSON
- safe metadata JSON
- correlationId
- optional idempotencyKey unique with task id
- createdAt

Add `FincaTaskAttachment` with:

- task id
- file URL, filename, content type, size, and Blob pathname
- attachmentType: `progress_photo` or `completion_photo`
- optional Telegram source message id
- uploader actor id/name
- optional idempotencyKey unique with task id
- createdAt

Never hard-delete tasks, events, or attachments through the agent contract.

## Tool Authentication

Extend the existing Operations `agent_access` token payload with optional `allowedTools: string[]`.

When `allowedTools` is present and non-empty, `/api/tools` must reject any tool name outside it with `TOOL_NOT_ALLOWED`. Preserve compatibility for existing tokens that do not yet carry the claim.

The Finca token uses:

```json
{
  "typ": "agent_access",
  "iss": "owhub",
  "aud": "operations",
  "agentId": "finca",
  "credentialId": "finca-operations-v1",
  "actorLabel": "OW Finca",
  "permissions": ["operations.read", "operations.write"],
  "propertyIds": ["owlswatch"],
  "activePropertyId": "owlswatch",
  "allowedToolClassifications": ["read", "guarded_write"],
  "allowedTools": [
    "operations.finca.list_tasks",
    "operations.finca.get_task",
    "operations.finca.list_workers",
    "operations.finca.create_task",
    "operations.finca.update_task",
    "operations.finca.daily_report",
    "operations.finca.attach_photo"
  ]
}
```

Tokens are short-lived and HMAC-signed with the existing Operations agent secret. The agent must not receive arbitrary Operations write permission.

## Tool Interfaces

### `operations.finca.list_tasks`

Input:

```json
{
  "statuses": ["open", "in_progress", "blocked"],
  "priority": null,
  "assigneeWorkerId": null,
  "telegramUserId": null,
  "query": null,
  "includeCompleted": false,
  "limit": 200
}
```

Return safe task summaries with code, title, details, estimatedMinutes,
priority, status, progressPercent, blockedReason, assignee safe fields, latest
event, attachment count, and timestamps.

When `telegramUserId` is supplied for `mis tareas`, resolve the matching active worker. Return an empty list rather than unrelated tasks if the sender is not linked.

### `operations.finca.get_task`

Input: `{ "taskCode": "F-0042" }`.

Return the safe task detail, full event history, and attachment metadata/URLs.

### `operations.finca.list_workers`

Input: optional `{ "query": "Juan" }`.

Return only worker id, displayName, Telegram handle/id, active state, and optional employeeId. Return no employee profile, payroll, banking, identity, address, or salary fields.

### `operations.finca.create_task`

Classification: `guarded_write`.

Input:

```json
{
  "title": "Reparar la puerta de la bodega",
  "details": null,
  "estimatedMinutes": 180,
  "priority": false,
  "assigneeWorkerId": null,
  "assigneeName": null,
  "source": "telegram",
  "idempotencyKey": "telegram--100123-456",
  "actor": {
    "telegramChatId": "-100123",
    "telegramUserId": "6831734977",
    "telegramMessageId": "456",
    "telegramUsername": "steadydee",
    "telegramDisplayName": "Dennis"
  }
}
```

Agent calls require idempotencyKey. Repeated keys return the original task with `duplicate: true`. Auto-upsert the Telegram sender as a FincaWorker for future assignment and `mis tareas` resolution.

The local Finca tool derives the key from inbound Telegram chat/message metadata and overwrites a conflicting model-supplied key before calling Operations.

`estimatedMinutes` is optional estimated effort, not a due date. Accept only a
whole integer from 1 through 10,080. Preserve it in task summaries, details,
the creation audit event snapshot/metadata, and duplicate-idempotency results.

### `operations.finca.update_task`

Classification: `guarded_write`.

Input includes taskCode, one action, idempotencyKey, actor metadata, and only fields relevant to that action.

Allowed actions:

- `start`
- `progress` with integer progressPercent
- `block` with blockedReason
- `complete`
- `assign` with assigneeWorkerId/assigneeName or clearAssignee
- `priority` with priority boolean
- `cancel`
- `reopen`
- `note` with note
- `rename` with non-empty `title`
- `details` with exactly one of non-empty `details` or
  `clearDetails: true`
- `estimate` with exactly one of integer `estimatedMinutes` or
  `clearEstimatedMinutes: true`

Rules:

- Start sets in_progress but does not invent a percentage.
- Progress 1–99 sets in_progress; 100 completes.
- Blocking requires a reason and preserves progress.
- Completed/cancelled tasks reject start/progress/block until explicit reopen.
- Reopen resets status to open and progress to 0.
- Rename, details, and estimate are corrections to task metadata. Permit them on
  active or closed tasks without implicitly reopening the task.
- Estimate values use the same whole-minute range as creation: 1 through
  10,080. Clearing an estimate stores null.
- Cancellation is the agent meaning of delete/remove. Never hard-delete a task.
- Every mutation uses optimistic locking and creates an event.
- Repeated update idempotency keys return the existing result.

Extend `FincaTaskAction` and `UpdateFincaTaskInput` with:

```text
title?: string | null
details?: string | null
clearDetails?: boolean
estimatedMinutes?: number | null
clearEstimatedMinutes?: boolean
```

The tool runtime must accept and forward only fields relevant to the selected
action. Include title, details, and estimatedMinutes in previous/new audit state
snapshots so every edit is reviewable. No schema migration is required because
all three values already exist on `FincaTask`.

### `operations.finca.daily_report`

Input: `{ "timezone": "America/Bogota" }`.

Return all open, in-progress, and blocked tasks, ordered once under Priority, In progress, Pending, and Blocked. Also return deterministic Spanish `messages` split below 3,800 characters for Telegram. Exclude completed and cancelled tasks.

If empty, return `No hay tareas pendientes en la finca.`

## Attachment Endpoint

Add:

```text
POST /api/finca/tasks/:taskCode/attachments/upload
```

Require the same agent token and exact `operations.finca.attach_photo` allowed tool. Accept multipart fields:

- `files` or repeated `file`
- `attachmentType`
- `sourceTelegramMessageId`
- `idempotencyKeyPrefix`
- `actorJson`

Allow JPEG, PNG, WebP, HEIC, and HEIF images only. Limit 10 files and 10 MB per file. Store under `finca-tasks/<property>/<year>/<month>/...` in Vercel Blob.

Create attachment and event records. Repeated per-file idempotency keys must return existing attachments. Return structured attachment metadata and the updated task.

## Operations UI

Add sidebar section `Operations` with `Finca Tasks` linking to `/finca`.

`/finca` is a dense work queue with code, description, optional estimated
effort, priority, status, progress, assignee, latest update, and updated time.
Include filters for Active, Open, In progress, Blocked, Completed, Cancelled,
Priority, and assignee. Include optional estimated minutes/hours in the manual
create form.

`/finca/[id]` shows task fields, estimated effort, progress controls,
assignment, priority, block reason, notes, photo gallery, and chronological
audit history. Add compact manual controls to rename the task, edit/clear
details, and set/change/clear estimated effort. Format estimates compactly, for
example `45 min`, `1 h 30 min`, or `3 h`. Humans may perform the same audited
actions as Telegram. Do not provide hard delete.

## Tests And Acceptance

Use property `owlswatch-test` for deployed UAT even if test and production share a database.

Test create idempotency, estimatedMinutes validation/persistence/return values,
update idempotency, assignment resolution, progress invariants, block/unblock,
complete/reopen, cancellation, optimistic conflicts, audit snapshots,
attachment limits, duplicate attachment retry, and exact tool denial for
payroll/quotes/expenses/email.

Also test:

- rename validation, persistence, duplicate idempotency, and previous/new title
  audit values
- details set and clear, including mutually exclusive input validation
- estimate set/change/clear with the 1 through 10,080 range
- metadata edits on completed/cancelled tasks do not reopen them
- unrelated fields supplied for an action are rejected or ignored safely

Run local lint, tests, build, and `git diff --check`. Deploy the exact candidate to the stable Operations test alias and exercise every tool with a test-scoped Finca token before production.

Production verification should create one clearly labeled smoke task, start it, set progress, attach a photo, complete it, verify UI/audit, then cancel or archive it through a human Operations path. Do not give the agent hard-delete authority.
