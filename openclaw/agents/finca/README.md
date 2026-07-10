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

## Runtime

- Profile: `finca`
- Agent: `finca`
- Workspace: `~/.openclaw/workspace-finca-ops`
- Gateway port: `19501`
- Daily report: 07:00 America/Bogota through launchd

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
7. Schedule: launchd with an enable-file kill switch; smoke tests verify the tool catalog, mock behavior, and denied broad tools.
