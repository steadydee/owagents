# Telegram Watchdog

The owlswatch gateway can be running while Telegram updates are not being dispatched to agents. When that happens, Cuenta and Cotiza may stop replying even though the gateway and Telegram status look healthy.

The watchdog checks:

```sh
openclaw --profile owlswatch channels status --probe
```

It also checks the durable Telegram ingress spool:

```sh
~/.openclaw-owlswatch/telegram/ingress-spool-default
```

If the probe fails, a known Telegram handler failure such as `Bot not initialized` appears in the OpenClaw log, or a Telegram update remains spooled longer than `STALE_SPOOL_SECONDS` (default: 180), it restarts only the `owlswatch` gateway. It never touches the `frontier` profile.

Install:

```sh
./scripts/install-telegram-watchdog.sh
```

Uninstall:

```sh
./scripts/install-telegram-watchdog.sh uninstall
```

Logs:

```sh
tail -f /tmp/openclaw/owlswatch-telegram-watchdog.log
```

The restart cooldown defaults to 10 minutes so a temporary internet outage does not create a restart loop.
