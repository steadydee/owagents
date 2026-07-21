# Finca

Finca is the Owl's Watch Telegram task assistant for finca workers.

## Architecture

```text
OW Finca Telegram group
  -> OpenClaw finca profile
  -> narrow finca_tasks tools
  -> Operations task tools and attachment endpoint
  -> Operations /finca review surface
```

Operations owns tasks, progress, assignments, photos, and audit history. The agent interprets Spanish task language and calls narrow tools. It does not use conversation memory as task storage.

Task creation accepts optional estimated effort, such as `Limpiar ventanas,
est. 3 horas`. The agent normalizes clear minute/hour phrases to
`estimatedMinutes`; estimates never become due dates.

Operations keeps stable task codes for audit and idempotency, but those codes
are hidden from workers. In Telegram, workers refer to tasks naturally by their
description. The agent reads the current task list, resolves a unique match,
and asks about the competing descriptions only when the reference is ambiguous.

## Runtime

- Profile: `finca`
- Agent: `finca`
- Workspace: `~/.openclaw/workspace-finca-ops`
- Gateway port: `19501`
- Daily task report: 07:00 America/Bogota through launchd, with quiet 15-minute
  retries after 07:00 until the daily delivery stamp exists
- Daily check-in: 16:00 America/Bogota through launchd, with quiet 15-minute
  retries after 16:00 until the daily delivery stamp exists

The morning report is generated deterministically by Operations from all current
outstanding tasks and sent through the narrow Telegram tool. It does not depend
on the LLM or conversation memory.

The afternoon message is fixed and sent directly through the narrow Telegram tool:

```text
Buenas tardes. ¿En qué tareas avanzamos hoy?
```

Workers answer naturally. The agent resolves their descriptions against current
Operations tasks and asks only when more than one task remains plausible.
Outstanding-task lists remain available on demand.

Workers can write `comandos`, `ayuda`, `¿qué puedes hacer?`, or `help` to see a
compact Spanish guide. Help is local guidance and does not call Operations.

Production requires `FINCA_TASKS_MOCKS=0`. Mock mode exists only for deterministic local tests and must never be enabled in the live profile.

## Authorization

The Telegram group and every worker use numeric allowlists. The Operations credential is scoped to the exact `operations.finca.*` tools. All authorized group users have the same task-management authority in v1.

## New-Agent Checklist

1. Job: track finca tasks. Operations is the source of truth.
2. Review surface: Operations `/finca`; task changes are reversible/audited except attachments, which are retained.
3. Model decisions: interpret task intent. Tool decisions: validation, task transitions, idempotency, storage, report ordering, and uploads.
4. Identity: dedicated `finca` Operations credential with Telegram actor metadata on every write.
5. Idempotency: `telegram-{chatId}-{messageId}` for creates and `telegram-{chatId}-{messageId}-{taskCode}` for updates/uploads, enforced by Operations.
6. Untrusted input: Telegram text and photos. Blast radius is limited to the Finca task subsystem.
7. Schedule: launchd sends the Operations-backed task list at 7:00 AM and the
   fixed check-in at 4:00 PM without an LLM dependency; enable-file kill switches
   control delivery.
