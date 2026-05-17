# Main Agent Tool Notes

The main agent is intentionally tool-poor.

It should rely on normal OpenClaw chat replies and `session_status` only. It should not have receipt, quote, email, filesystem, shell, browser, gateway, cron, node, or web tools.

Tool availability is controlled in `~/.openclaw-owlswatch/openclaw.json`; this file is guidance only.
