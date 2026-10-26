# Runtime guard

No model-facing tools or business writes. Hooks bound each run to 40 tool calls,
12 search calls, 3 identical reads and ten minutes
before another tool may execute. OpenClaw's own timeout and loop detector remain
enabled. Budgets use trusted run context, never model arguments, and persist
across provider attempts that reuse a run ID. Healthy input gates return an
explicit `pass`, as required by the installed OpenClaw hook runner.

Provider credit failures create a durable 30-minute cooldown. Subsequent requests
receive a deterministic degraded-state explanation unless the selected agent's
trusted configuration contains a different provider that its model policy allows.
In that case the guard permits OpenClaw to attempt its configured chain, including
when the run's provider is not known yet. This is not evidence that the alternate
has valid credentials or is healthy. It neither selects a provider nor clears the
circuit or run budgets. Aliases, unknown provider attribution and ambiguous agent
configuration cannot bypass the credit stop. A per-agent primary does not inherit
default fallbacks; an explicit empty fallback list also disables inheritance.
Other explicitly identified providers are unaffected. An operator can remove
`health/provider-circuit.json` after verifying credit recovery. This gate does not
stop an already active run or replace provider retry handling.

`health/runs.jsonl` contains agent/provider/model, token counters, durations,
sanitized outcome codes and truncated SHA-256 run/call correlation keys. It
excludes prompts, replies, arguments, raw identifiers and credentials. Rotate at
5 MB, retaining one prior file.

`model_call_ended` records each observed completed provider request, including
request errors that later recover. `llm_output` reports aggregate usage for a
whole attempt, which can contain many provider requests, and can arrive after
`agent_end`. Usage is retrospective: these hooks cannot enforce a live cumulative
token budget. Token counts are not dollar charges; provider pricing, cache rates
and provider billing records are needed for costs. A process crash before the
hooks finish can leave incomplete telemetry.

`run_end` is a revisioned snapshot. Consumers must fold by `runKey`, taking the
latest snapshot, before counting runs. The guard checks terminal assistant error
metadata as well as the harness status, then revises final counts and outcome
when late hooks arrive. `scripts/agent-health.py` performs this fold, counts
requests separately from `attemptsWithUsage`, and retains historical failures
without raising current alerts after 24 hours. Runtime success is not a receipt
for a successful business action.

`after_tool_call` also records `business_outcome` for explicitly supported,
successful tools that returned a durable artifact identifier. Only a category
and hashed identifier enter the journal, never result bodies or raw IDs. The
local operations report deduplicates these outcomes separately from completed
responses; a saved draft or processed email intake is not a human approval or
proof of delivery. See `docs/runbooks/operations-observability-recovery.md`.

The health reader also exposes unfinished run age. A latest start with no end
warns after 15 minutes; a newer run supersedes it, and starts older than 24 hours
are historical only. This detects missing completion telemetry, including a
crash, without claiming the process is still running or restarting it.

Run `node --test tools/agent_runtime_guard/tests/*.test.mjs`.

The OpenClaw 2026.10.5 migration has different built-in loop and health limits;
see `docs/runbooks/openclaw-10.5-controls.md` for the remaining activation gates.
