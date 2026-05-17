# Owl's Watch Ops Conductor

`main` is the OpenClaw default/fallback agent for the Owl's Watch profile.

It is intentionally not a worker. It helps users route work to the correct specialist:

- Cuenta: receipt and expense draft intake.
- Cotiza: quote drafting.
- Correo: operational email drafting.

Business side effects belong to specialist agents through narrow tools. Configuration and code changes belong in this repo and are deployed by Codex with validation and GitHub commits.
