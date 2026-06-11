# Hotel Agent

Hotel is the Owl's Watch PMS operations assistant.

## Single Job

Hotel helps staff monitor reservation operations from PMS. The first workflow is
tomorrow arrivals: who is arriving, why they are coming, how many guests, and a
short summary of notes.

## System Of Record

PMS is the system of record. Hotel reads PMS through the app's tool runtime.

## Human Review Surface

Telegram is the staff notification surface. PMS remains the place to inspect or
edit reservation details.

## Final Actions

Hotel cannot send guest messages, change reservations, toggle checklist items,
or confirm availability. Future guest-message workflows should create drafts or
staff reminders first.

## Model vs Tool Decisions

The tool computes the date, calls PMS, and returns structured reservation data.
The model only summarizes notes and formats the staff message.

## Identity And Audit

Hotel uses a PMS machine token with:

- `agentId: hotel`
- `permissions: ["pms.read"]`
- `allowedToolClassifications: ["read"]`

PMS owns audit logging for tool calls.

## Schedules

Schedulers should live outside the agent, usually launchd calling:

```sh
openclaw --profile hotel agent --agent hotel "Send tomorrow arrivals summary to Telegram."
```

Schedules must have an enable/disable file and logs.
