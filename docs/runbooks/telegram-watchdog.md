# Telegram Watchdogs

An OpenClaw gateway can be running while Telegram updates are not being
dispatched to agents. Each Telegram profile therefore has its own watchdog,
lock, restart cooldown, and logs. A watchdog only restarts its own profile.

The watchdog checks:

```sh
openclaw --profile owlswatch channels status --probe
openclaw --profile hotel channels status --probe
```

It also checks the durable Telegram ingress spool:

```sh
~/.openclaw-owlswatch/telegram/ingress-spool-default
```

If the probe fails, a known Telegram handler failure such as `Bot not
initialized` appears in the OpenClaw log, or a Telegram update remains spooled
longer than `STALE_SPOOL_SECONDS` (default: 180), it restarts only the affected
gateway. It never touches the `frontier` profile.

Install:

```sh
./scripts/install-telegram-watchdog.sh
./scripts/install-hotel-watchdog.sh
```

Uninstall:

```sh
./scripts/install-telegram-watchdog.sh uninstall
./scripts/install-hotel-watchdog.sh uninstall
```

Logs:

```sh
tail -f /tmp/openclaw/owlswatch-telegram-watchdog.log
tail -f /tmp/openclaw/hotel-telegram-watchdog.log
```

The restart cooldown defaults to 10 minutes so a temporary internet outage does not create a restart loop.

These are user LaunchAgents. After a reboot they start when the `agent` macOS
account logs in. Keep automatic login enabled on the dedicated Mac mini and
verify it after password changes:

```sh
sysadminctl -autologin status
```

FileVault must remain off for automatic login. The Mac should also have
`autorestart 1` in `pmset -g custom` so it powers back on after an outage.
After changing the `agent` account password, turn automatic login off and back
on in macOS Login Options so `/etc/kcpassword` is refreshed; the status command
can still name the user when the stored password is stale.
