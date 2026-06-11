# Add A New Agent

0. Read `docs/agent-design-guidelines.md` and answer its new-agent checklist in the agent's README.
1. Create `openclaw/agents/<agent-id>/`.
2. Add `IDENTITY.md`, `AGENTS.md`, `SOUL.md`, `TOOLS.md`, `USER.example.md`, `MEMORY.template.md`, `README.md`, and skills. Write each file for this agent; do not leave stock OpenClaw template text.
3. Add narrow tools under `tools/<tool-server>/` if needed, with `README.md`, `requirements.txt`, and tests for any deterministic logic.
4. Update `openclaw/profiles/owlswatch/openclaw.example.json`, `docs/security-boundaries.md`, and the agent's `TOOLS.md` together.
5. Add smoke tests.
6. Run `scripts/check-no-secrets.sh`.
7. Deploy to the Mac mini and validate OpenClaw.
