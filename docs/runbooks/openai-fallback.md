# OpenAI fallback for the active fleet

The quickest supported path is to keep the installed OpenClaw 2026.7.1-2 runtime
and add `openai/gpt-5.4-mini` as an API-key backup. The default preparer mode is
`--runtime-generation legacy`. It does not upgrade the runtime, migrate agent
state, change channel ownership, or alter business-tool grants. Luna and retired
Finca remain excluded. Preparation alone does not enable production fallback.

Keep each agent's current primary model reference and settings. Explicit
per-agent model objects need their own fallback list; defaults alone do not
override a strict agent model. The backup uses the official Responses endpoint,
SSE transport and an explicit `agentRuntime.id: openclaw`, so it cannot silently
select subscription OAuth or the Codex runtime. API billing is separate from a
ChatGPT subscription.

## Verified compatibility

On 2026-10-26, after DHCP renewal restored IPv4, the installed 7.1-2 runtime passed
an isolated forced-503 failover to GPT-5.4-mini in about 5.5 seconds. The actual
runtime guard allowed the configured backup despite a pre-existing primary
credit circuit, recorded a completed response and retained that circuit. A
separate synthetic tool fixture passed with exactly one allowed local tool call.
These are test results, not production response-time guarantees.

The same day's credentialed synthetic DeepSeek API requests returned HTTP 200
for both `deepseek-reasoner` and `deepseek-flash`; both reported response model
`deepseek-flash`. Although the official retirement notice names the old IDs,
these requests show the provider still accepted the reasoner alias at that time.
Do not confuse the new 10.5 runtime's local catalog rejection with a verified
provider-side rejection. Legacy preparation preserves current primary references
and thinking settings rather than requiring an unrelated primary-model change.
The Nómina evaluation harness remains pinned to the legacy release's reasoner
reference and strictly compares the configured production model. Its direct
provider tests do not establish OpenAI payroll workflow acceptance.

The installed runtime can still discard validated IPv6 candidates. Legacy fallback
therefore depends on working IPv4; DHCP recovery does not fix that runtime bug.
Keep network monitoring and the separately gated runtime upgrade plan.

## Limits and credentials

- Primary request timeout: at most 30 seconds; backup: 60 seconds.
- Zero same-provider retries in each agent's `settings.json` under
  `retry.provider.maxRetries`. This is not an `openclaw.json` setting. A synthetic
  primary 503 with a 120-second Retry-After hint must promptly reach the backup.
- Keep tighter whole-run limits; otherwise cap at 600 seconds. Idle heartbeats off.
- Keep `agents.defaults.contextTokens` in legacy mode, at most 65,536, and preserve
  tighter selected model-row limits. The tested 7.1-2 schema supports both fields.
  Backup output is capped at 4096 tokens; primary DeepSeek output at 8192.
- Preserve existing numeric loop and health-monitor policies on legacy. The
  different 10.5 limits are not prerequisites for this rollout.
- Model success does not prove business completion or delivery. Existing durable
  attempt/reconciliation workflows remain mandatory after uncertain writes.

For gateways, scheduled jobs and command-line runs, prefer a profile-owned file:

```sh
python3 scripts/prepare-openai-fallback.py --profile owlswatch \
  --credential-file /absolute/home/.openclaw-owlswatch/credentials/openai-fallback.key
```

The file must be provisioned separately with the authorized OpenAI provider key,
mode 0600 and the service's correct owner. The preparer only checks its canonical
absolute path is inside that profile's `credentials` directory. It never reads,
creates or copies the key. OpenClaw performs its normal file ownership and mode
checks; no `allowInsecurePath` exception is enabled. Configuration uses a
`source: file`, `mode: singleValue` secret provider and an API-key SecretRef with
`id: value`. Do not redistribute app credentials or change a tool-scoped key.

Portable examples retain environment mode. Use `--credential-env VARIABLE` when
all intended launchers can reliably supply that authorized key. File and env
options are mutually exclusive. Another profile's credential path is refused.
No key is automatically created, shared, rotated or displayed.

## Prepare and release

1. Merge the source, fetch, and require a clean exact `origin/main` through
   `scripts/assert-release-ready.sh`. Never deploy the candidate branch.
2. Capture an encrypted profile backup and verify the restore drill. Preserve
   pending spools, sessions, task identities, configuration and settings. The
   legacy fallback does not require database or agent-schema migration.
3. Provision the authorized provider credential separately per target profile.
   A missing key is a rollout blocker, not a reason to reuse another domain's
   app token or fall back to subscription auth.
4. Inspect the preparer's content-free plan. Add
   `--output-dir /private/tmp/NEW-PRIVATE-DIRECTORY` to write 0600 configuration
   and settings candidates outside source and live state. The directory must be
   new. Candidates contain existing profile secrets and must not enter Git or
   ordinary reports.
5. In disposable state, validate the complete candidate with the installed CLI,
   preserving routes, sender allowlists, grants and native approval controls.
   Apply each settings candidate to its matching isolated agent directory. Audit
   session/model overrides separately; preparation does not rewrite sessions.
6. Run synthetic forced-fallback, tool and actual business-workflow fixtures with
   fake external writes. Include unknown-outcome replay and native approval gates.
   Successful plugin loading or a generic model answer is insufficient.
7. Deploy one authorized profile at a time with its existing single supervisor.
   Hotel remains system-owned; its unused GUI plist stays disabled. Check
   gateway authentication, channel health, model completion, workflow results and
   actual response delivery without unsolicited test messages. Record deployed
   hashes. Correo's scheduler stays disabled until its separate closure rollout.
8. Record the exact selected runtime for monitoring and scheduled jobs. A legacy
   shared installation may be explicitly recorded with `legacy: true`; missing
   metadata must not silently select a global runtime. Never upgrade that shared
   installation as part of fallback activation.

The preparer refuses unknown profiles/agents, mixed schemas, reverse migration
from keyed agents to legacy, conflicting secret providers, custom OpenAI endpoints
and request overrides. It preserves application identities, auth profiles, routes,
tool policies and original primary modes. Complete validation is still required.

## Reproducible synthetic tests

Supply the authorized key privately in `OPENAI_FALLBACK_API_KEY`, then use the
explicit reviewed Node and CLI paths. The helper never discovers a live profile.

```sh
python3 scripts/uat-openai-fallback.py --schema legacy \
  --node /reviewed/node --openclaw-cli /reviewed/openclaw/openclaw.mjs \
  --output-dir /private/tmp/NEW-CANARY --mode failover \
  --guard-plugin /path/to/released/tools/agent_runtime_guard \
  --primary-credit-cooldown --credential-storage file
```

The `file` option puts the supplied key only in a disposable 0600 file; no API-key
variable is passed to the runtime child. Default `env` uses an environment
SecretRef. Both use a temporary home, empty state, no channels or business tools,
and a restricted child environment. The tool mode exposes one local fixture
lookup. Synthetic state and the temporary key are removed on completion; the
agent process group is terminated at the 60-second outer deadline. Failure exits
nonzero. Use separate output directories to retain distinct evidence.

Modes are `direct`, `failover`, `tools` and `network-failover`. The latter uses a
fake DeepSeek key and only passes on an observed transport failure. Reachable
DeepSeek rejecting that fake key is not a network-failover pass. It is not needed
for the now-working IPv4 path. Guarded failover additionally requires completion
telemetry and preservation of the original synthetic credit circuit.

## Optional runtime migration

`--runtime-generation 2026.10.5` retains the separately gated migration path. It
canonicalizes retired primary references to Flash, transfers default context
limits into explicit model rows and enables the external DeepSeek provider.
Follow `isolated-runtime.md` and `openclaw-10.5-controls.md`: copy/migrate state,
install the pinned official provider beside the isolated core, validate keyed
agent/model-policy schemas, accept or replace changed loop/health controls and
prove rollback before any activation. Use `--schema 2026.10.5` for its canaries;
network-failover also needs `--deepseek-plugin` pointing to the package directory.
No shared installation or excluded agent may be changed.

## Cost

The official GPT-5.4-mini page lists $0.75 per million input tokens, $0.075 cached
input and $4.50 output. A 26,000-token uncached input plus 1,000 output tokens is
about $0.024 for one call; a task may use several calls. Keep it as backup and
measure cost per completed business task. Token counters do not replace provider
billing or an account/project budget.

Sources: [OpenAI model](https://developers.openai.com/api/docs/models/gpt-5.4-mini),
[DeepSeek retirement notice](https://api-docs.deepseek.com/news/news260424/),
[DeepSeek thinking control](https://api-docs.deepseek.com/guides/thinking_mode/).
