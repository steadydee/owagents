# OW Finca Task Tools

This package exposes only the task actions needed by the Finca OpenClaw agent.

## Production Contract

The tools mint short-lived, exact-tool-scoped Operations agent tokens and call:

- `operations.finca.list_tasks`
- `operations.finca.get_task`
- `operations.finca.list_workers`
- `operations.finca.create_task`
- `operations.finca.update_task`
- `operations.finca.daily_report`
- `/api/finca/tasks/:taskCode/attachments/upload`

The required Operations contract is documented in `docs/finca-operations-contract.md`.

Task creation may include optional `estimatedMinutes` from 1 through 10,080.
The agent converts explicit minute/hour wording to whole minutes. Operations
stores and displays the value; neither layer turns it into a due date.

Before create or assignment writes, the tool checks the safe Operations worker
list. An exact full name wins. A single unique partial match is accepted.
Multiple matches return `worker_ambiguous` with the safe display-name options
and make no task change.

## Runtime Variables

- `OPERATIONS_BASE_URL=https://operations.owlswatch.com`
- `OPERATIONS_PROPERTY_ID=owlswatch`
- `OW_AGENT_TOKEN_SECRET_FILE=~/.openclaw-finca/secrets/operations-agent-secret`
- `FINCA_TELEGRAM_NOTIFY_CHAT_ID=<private_group_id>`
- `FINCA_TASKS_MOCKS=0`

The Telegram bot token is read from the OpenClaw channel config or `FINCA_TELEGRAM_BOT_TOKEN`. Never commit it.

## Mock Mode

`FINCA_TASKS_MOCKS=1` is accepted only for deterministic local tests. The production profile example sets it to `0`.

## Photos

Inbound photos are copied into the Finca workspace spool before upload. Album state and claims are durable. Failed uploads leave a `pending-upload.json` marker and the photos in the spool for recovery.
