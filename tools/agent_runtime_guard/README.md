# Runtime guard

No model-facing tools or business writes. Hooks bound each run to 40 tool calls,
12 search calls, 3 identical reads and ten minutes
before another tool may execute. OpenClaw's own timeout and loop detector remain
enabled. Budgets use trusted run context, never model arguments, and persist
across provider attempts that reuse a run ID. Healthy input gates return an
explicit `pass`, as required by the installed OpenClaw hook runner.

Provider credit failures create a durable 30-minute cooldown. Subsequent requests
receive a deterministic degraded-state explanation. An operator can remove
`health/provider-circuit.json` after verifying credit recovery. Other explicitly
identified providers are unaffected. This gate does not stop an already active
run or replace provider retry handling.

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

The health reader also exposes unfinished run age. A latest start with no end
warns after 15 minutes; a newer run supersedes it, and starts older than 24 hours
are historical only. This detects missing completion telemetry, including a
crash, without claiming the process is still running or restarting it.

Run `node --test tools/agent_runtime_guard/tests/*.test.mjs`.
