# Owl's Watch Quote Tools

This plugin exposes narrow tools for Cotiza.

Tools read tokens and credentials from environment variables or `~/.openclaw-owlswatch/openclaw.json`. Tokens are never accepted as tool parameters and never returned in structured output.

Production mode calls the configured Operations APIs and Google APIs. Mock mode is available only when `OWLSWATCH_QUOTES_MOCKS=1` and is meant for local contract tests while Operations quote endpoints or Google credentials are not ready.
