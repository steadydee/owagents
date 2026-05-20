# Brain Intake Agent

Brain Intake is the OpenClaw-side worker for Dennis's private Brain/Command Center Telegram space.

It keeps Telegram runtime ownership in OpenClaw and meaning/state ownership in Brain:

Telegram -> OpenClaw gateway -> Brain Intake agent -> Brain API -> Telegram receipt

## Scope

- Text-only Brain capture.
- Brain receipt delivery.
- No external side effects beyond returning the receipt.

## Setup

The live OpenClaw profile should include:

- A `brain` agent.
- The `brain_intake` MCP server.
- A route binding from the private Dennis Brain Telegram group to `agentId: "brain"`.

The Brain app should be reachable at `BRAIN_API_BASE_URL`, defaulting to `http://127.0.0.1:3000`.

If `BRAIN_ADMIN_TOKEN` is set for the Brain app, configure the same token in the runtime-only OpenClaw MCP env for `brain_intake`. Do not commit it.
