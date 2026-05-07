# Add A New Agent

1. Create `openclaw/agents/<agent-id>/`.
2. Add `IDENTITY.md`, `AGENTS.md`, `USER.example.md`, `MEMORY.template.md`, `README.md`, and skills.
3. Add narrow tools under `tools/<tool-server>/` if needed.
4. Update `openclaw/profiles/owlswatch/openclaw.example.json`.
5. Add smoke tests.
6. Run `scripts/check-no-secrets.sh`.
7. Deploy to the Mac mini and validate OpenClaw.

