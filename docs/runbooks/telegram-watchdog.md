# Telegram Recovery

OpenClaw owns Telegram long-poll recovery and channel-health restarts. Each
gateway also runs under a macOS LaunchAgent with `KeepAlive` and `RunAtLoad`, so
launchd restores a process that exits. Do not install a second restart loop.

Check the live profiles with:

```sh
openclaw --profile owlswatch channels status --probe
openclaw --profile hotel channels status --probe
openclaw --profile finca channels status --probe
```

OpenClaw durably spools accepted Telegram updates under:

```sh
~/.openclaw-owlswatch/telegram/ingress-spool-default
~/.openclaw-hotel/telegram/ingress-spool-default
~/.openclaw-finca/telegram/ingress-spool-default
```

Do not restart a gateway merely because a spooled update is old. A startup
handler can leave an update retryable; restarting during that replay can
advance the offset without delivering a response.

Remove the retired external watchdog LaunchAgents:

```sh
./scripts/remove-external-telegram-watchdogs.sh
```

Diagnose a real failure with OpenClaw's own probes and logs:

```sh
openclaw --profile finca status --deep
openclaw --profile finca channels status --probe
openclaw --profile finca logs --follow
```

If OpenClaw reports false polling stalls during otherwise healthy long-running
work, tune `channels.telegram.pollingStallThresholdMs` within OpenClaw rather
than adding a second watchdog. Export diagnostics before manual restarts when
the failure is repeatable. See the official [Telegram channel guide](https://docs.openclaw.ai/channels/telegram)
and [health checks guide](https://docs.openclaw.ai/health).

The gateway services are user LaunchAgents. After a reboot they start when the
`agent` macOS account logs in. Keep automatic login enabled on the dedicated
Mac mini and verify it after password changes:

```sh
sysadminctl -autologin status
```

FileVault must remain off for automatic login. The Mac should also have
`autorestart 1` in `pmset -g custom` so it powers back on after an outage.
After changing the `agent` account password, turn automatic login off and back
on in macOS Login Options so `/etc/kcpassword` is refreshed; the status command
can still name the user when the stored password is stale.
