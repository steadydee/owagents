# Agent Architecture

Owl's Watch agents run on the Mac mini under the `owlswatch` OpenClaw profile.

```mermaid
flowchart LR
  Telegram["Telegram topics"] --> Cuenta["Cuenta"]
  Telegram --> Cotiza["Cotiza"]
  Telegram --> Correo["Correo"]
  Gmail["Owl's Watch Gmail"] --> Cotiza
  Gmail --> Correo
  Cuenta --> IntakeTools["owlswatch_intake tools"]
  Cotiza --> QuoteTools["owlswatch_quotes tools"]
  Correo --> EmailTools["owlswatch_email tools"]
  IntakeTools --> Operations["Operations app"]
  QuoteTools --> Operations
  EmailTools --> Operations
  EmailTools --> Luna["Luna context"]
  QuoteTools --> Drive["Google Drive quote sheets"]
```

## Source Of Truth

Operations is the system of record for expenses and quotes.

Agents create drafts only:

- Cuenta creates expense drafts.
- Cotiza creates quote drafts and revised quote drafts.
- Correo creates Email Desk draft tasks and optional Gmail drafts. Correo never sends email.

Google Drive stores editable quote sheets, but Operations remains canonical for IDs, status, totals, assumptions, and review.

## Repo Versus Runtime

This repo stores source and templates. The live runtime lives under:

- `~/.openclaw-owlswatch/`
- `~/.openclaw/workspace-owlswatch/`
- `~/.openclaw/workspace-owlswatch-cotiza/`
- `~/.openclaw/workspace-owlswatch-correo/`

Do not commit runtime sessions, memory logs, spools, service-account JSON, auth state, or real `openclaw.json`.
