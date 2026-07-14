# Hotel Agent

Hotel is the Owl's Watch PMS operations assistant.

## Single Job

Hotel helps staff monitor reservation operations from PMS. The first workflow is
tomorrow hotel activity: who is arriving, who is checking out, who is staying
another day, and a short summary of operational notes. Hotel can also create new
reservations through a guarded two-step staff confirmation flow.

## System Of Record

PMS is the system of record. Hotel reads PMS through the app's tool runtime.

## Human Review Surface

Telegram is the staff notification surface. PMS remains the place to inspect or
edit reservation details.

## Final Actions

Hotel cannot send guest messages, modify/cancel/delete reservations, toggle
checklist items, or confirm availability. New reservation creation requires a
PMS-prepared draft and a simple staff `sí` confirmation. Future guest-message
workflows should create drafts or staff reminders first.

## Model vs Tool Decisions

The tool computes the date, calls PMS, and returns structured reservation data.
The model only summarizes notes and formats the staff message.

## Identity And Audit

Hotel uses scoped PMS machine tokens with:

- `agentId: hotel`
- read profile: `permissions: ["pms.read"]`
- prepare profile: `allowedTools: ["agent_prepare_reservation"]`
- create profile: `allowedTools: ["agent_create_reservation"]`

PMS owns audit logging for tool calls.

## Schedules

Schedulers should live outside the agent, usually launchd calling:

```sh
openclaw --profile hotel agent --agent hotel "Send tomorrow hotel summary to Telegram."
```

Schedules must have an enable/disable file and logs.
