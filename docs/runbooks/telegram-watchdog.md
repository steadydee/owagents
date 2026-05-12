# Telegram Watchdog

The owlswatch gateway can be running while the Telegram polling channel is stopped. When that happens, Cuenta and Cotiza will not receive Telegram messages even though the gateway status looks healthy.

The watchdog checks:

```sh
openclaw --profile owlswatch channels status --probe
```

If Telegram is not both `running` and `connected`, it restarts only the `owlswatch` gateway. It never touches the `frontier` profile.

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

