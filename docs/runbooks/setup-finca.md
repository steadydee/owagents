# Set Up OW Finca

Do this only after the Operations Finca contract is deployed and its test UAT passes.

## Telegram

1. Create a BotFather bot for OW Finca.
2. In BotFather, disable privacy mode for that bot.
3. Create a private Telegram group named `OW Finca`.
4. Add the bot to the group.
5. Add only Owl's Watch staff who should manage tasks.
6. Send one message from each worker so their numeric user IDs can be captured.

Do not use a wildcard sender policy. Group membership does not replace the numeric allowlist.

## Runtime Config

Edit `~/.openclaw-finca/openclaw.json`:

- set `channels.telegram.botToken`
- set `channels.telegram.enabled` to true
- add every numeric user ID to `allowFrom`, `groupAllowFrom`, and the group-level `allowFrom`
- add the numeric group chat ID under `groups`
- set `FINCA_TELEGRAM_NOTIFY_CHAT_ID` to that group ID
- keep `FINCA_TASKS_MOCKS` equal to `0`

Store the Operations agent signing secret only at:

```text
~/.openclaw-finca/secrets/operations-agent-secret
```

Set mode `600`. Do not place it in agent workspace files or git.

## Verify

```sh
./scripts/deploy-finca-to-mac-mini.sh
./scripts/smoke-finca.sh
openclaw --profile finca config validate
openclaw --profile finca skills check --agent finca
openclaw --profile finca channels status --probe
openclaw --profile finca security audit --deep
openclaw --profile finca gateway restart
```

Test with an allowlisted user:

```text
Tarea: prueba OW Finca. Es prioridad.
Lista de tareas
Empiezo la prueba de OW Finca
La prueba de OW Finca va en 50%
Terminamos la prueba de OW Finca
```

Test that a non-allowlisted account receives no agent response and cannot change Operations.

After the end-to-end test passes:

```sh
./scripts/install-finca-watchdog.sh
./scripts/install-finca-schedule.sh
./scripts/run-finca-daily-checkin.sh --force
```

The launchd schedule sends `Buenas tardes. ¿En qué tareas avanzamos hoy?` at
16:00 America/Bogota. The fixed message bypasses the LLM. Do not run the forced
command in production unless an immediate test message is intended.
