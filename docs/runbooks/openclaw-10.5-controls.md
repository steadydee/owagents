# OpenClaw 2026.10.5 control compatibility

Reviewed against the installed candidate package on 2026-10-26. This is source
and isolated SDK evidence, not a live deployment or proof of gateway recovery.
The target profiles are owlswatch, hotel, bailey-finance and nomina. Luna and the
retired Finca service remain outside this migration.

## Loop limits are not equivalent

The 2026.10.5 public configuration retains only `tools.loopDetection.enabled`,
including per-agent overrides. Preserve `true` for this fleet. Removing the old
numeric fields makes configuration valid but does not preserve their behavior.

| Control | Previous fleet policy | 2026.10.5 built-in behavior |
| --- | --- | --- |
| Rolling history | 30 | 30 |
| Generic repeat warning / critical | 4 / 8 globally; 3 / 5 for Cobros | 10 / 20; critical requires identical outcomes |
| Global circuit breaker | 12 globally; 8 for Cobros | 30 |
| Unknown-tool threshold | 3 globally; 2 for Cobros | 10 |
| Detector switches | Generic repeat, no-progress polling and ping-pong enabled | No individual public switches; enabled flag controls rolling detectors |
| Identical consecutive tool errors | Separate from these configured thresholds | Always ends turn after 3 identical tool/arguments/error results, even if loop detection is disabled |
| Compaction retry loop | Separate control | Enabled unless explicitly disabled; default window of 3 identical tool/arguments/result observations after compaction |

The first rolling critical detection blocks the tool batch and gives the model
one further response opportunity with tools; a second critical detection without
new user input ends the run. Polling/wait and ping-pong detectors also have fixed
internal thresholds. Do not treat every critical threshold as an immediate
whole-run stop.

The custom guard still blocks the fourth identical read, thirteenth search,
forty-first tool call and the next tool after ten elapsed minutes. An offline
native SDK test verified those limits. It also allowed eight identical mutating
tool calls: this guard does not replace the former stricter generic, unknown-tool
or Cobros limits. Tool idempotency and the native detectors remain necessary.
Token counters are retrospective and do not enforce a live token budget.

Source evidence inside the pinned package: `docs/tools/loop-detection.md`,
`dist/tool-loop-detection-config-*.mjs`, `dist/tool-loop-detection-*.mjs` and
`dist/embedded-agent-*.mjs`. The former values are in the previous profile examples
and `scripts/harden-runtime.py`. An old hardener that writes these removed fields
must not be applied after migration.

## Health checks use fixed defaults

Hotel and Nomina previously configured `gateway.channelHealthCheckMinutes: 1`,
`channelStaleEventThresholdMinutes: 5` and `channelMaxRestartsPerHour: 6`.
These numeric configuration fields are removed. Public channel configuration
supports `channels.<provider>.healthMonitor.enabled` and per-account overrides.

The 2026.10.5 gateway starts its monitor with the internal defaults:

| Control | 2026.10.5 default |
| --- | --- |
| Initial monitor grace | 1 minute |
| Check cadence | 5 minutes |
| Restart cooldown | 2 check cycles, or 10 minutes |
| Restart limit | 10 per hour |
| Busy run with stale activity | 25 minutes |
| Stale transport event | 30 minutes |
| Connect / reconnect grace | 2 minutes |

These are distinct signals and do not promise a recovery deadline. The monitor
skips manual stops, suppressed accounts, terminal disconnects and blocked states;
it also defers while the channel's own restart is already scheduled. A quiet
channel without incoming messages is not itself unhealthy. The channel's own
crash retry ladder remains separate from the health monitor's restart limit.

Source evidence: `docs/gateway/health.md`,
`dist/server-runtime-startup-services-*.mjs` (`startGatewayChannelHealthMonitor`
passes no numeric configuration overrides), and
`dist/channel-health-policy-*.mjs`. In particular, `15e5` milliseconds means
25 minutes, and `18e5` milliseconds means 30 minutes.

No equivalent public numeric replacement was found. Slower checks, changed stale
signals and changed restart behavior remain an explicit activation decision. Do
not add an outside restart supervisor or patch the shared runtime to imitate the
old settings.

## Custom hook evidence and remaining gates

The isolated harness registered the actual plugin through 2026.10.5's native
registry, hook runner and before-run gate. All six hooks registered without
diagnostics: `before_agent_run`, `before_tool_call`, `after_tool_call`,
`model_call_ended`, `llm_output` and `agent_end`. It verified read/search/total/time
budgets plus usage, observed response completion and verified business outcomes
using synthetic tool results. Network access was replaced with a throwing stub;
no channels, model requests, credentials or business writes were involved.

The plugin requires
`plugins.entries.owlswatch-runtime-guard.hooks.allowConversationAccess: true`.
Without it, the registry skips the three conversation hooks (`before_agent_run`,
`llm_output`, `agent_end`). Incognito runs can omit completion hooks; do not claim
complete telemetry for them. Hook success does not prove full plugin discovery,
production routing or end-to-end fallback.

The credit regression was reproduced and fixed: a durable DeepSeek circuit now
permits a run whose trusted selected-agent configuration explicitly includes an
allowed OpenAI provider. Both missing initial provider identity and an identified
DeepSeek primary pass. A no-fallback agent remains blocked, and the durable circuit
and tool budgets remain intact. Configuration is only permission to attempt an
alternate; OpenClaw still owns authentication, model eligibility and retries.
`modelPolicy.allow: []` means allow-any in the pinned SDK documentation; per-agent
policy replaces defaults. Alias-only alternate references are conservatively not
counted by this guard.

Before activation, require a disposable full-runtime forced-failure canary with
the plugin loaded and a pre-existing credit circuit. Verify both configured model
chains and session/model overrides, because the guard does not resolve session
overrides itself. Confirm all routing, grants, native approvals, selected runtime
metadata, recovery checkpoint and rollback evidence separately. Resolve or
explicitly accept the loop/health control differences above. These checks do not
authorize activation from an unmerged or dirty branch.
