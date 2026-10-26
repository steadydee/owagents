# OpenAI fallback for the active fleet

This is a staged release, not an enabled production fallback. Luna and retired
Finca are excluded. The preparer never edits live configuration, starts a
gateway, sends a message, or reads a business record.

Use `openai/gpt-5.4-mini` through the native OpenClaw runtime, official Responses
endpoint, and API-key authentication. Keep DeepSeek as primary, migrating retired `deepseek-reasoner` /
`deepseek-chat` references to `deepseek/deepseek-flash`. Preserve their thinking
on/off intent. Other explicit primary selections remain unchanged when an explicit catalog row can preserve their context limit; otherwise preparation stops for reconciliation. Explicit
per-agent model objects need their own fallback list; defaults alone do not
override a strict agent model. API billing is separate from a ChatGPT subscription.
The OpenAI credential is a runtime environment SecretRef, never a model argument
or a checked-in value. Existing app identities and credentials remain unchanged.

The isolated runtime candidate is OpenClaw 2026.10.5 with Node 24.22.1. Install the separately published official `@openclaw/deepseek-provider@2026.10.5`
next to that isolated core; OpenAI and Telegram are bundled. The provider must
resolve the matching isolated SDK. Its guarded
DNS lookup retains validated IPv6 candidates when IPv4 is unavailable. The old
2026.7.1-2 runtime can discard those candidates. Do not upgrade the shared global
installation: it is used by excluded services.

## Limits and evidence

- Explicit canonical Flash catalog seed, so discovery outages do not prevent
  primary resolution or fallback. Primary output cap 8192 tokens.
- Primary HTTP request limit: 30 seconds; backup request limit: 60 seconds.
- Zero same-provider retries, in each agent's `settings.json` under
  `retry.provider.maxRetries`. A failed primary can advance to the backup
  without spending the run budget on retry backoff.
  This is an embedded runtime setting, not an `openclaw.json` field.
- Keep existing shorter whole-run limits; otherwise cap at 600 seconds.
- Cap active context at 65,536 tokens in each selected provider model row, preserving any tighter current limit. The retired `agents.defaults.contextTokens` field is removed only after transferring that limit; the 10.5 doctor otherwise drops it.
- Backup output cap 4096 tokens, low reasoning, SSE transport. Idle heartbeats off.
- Synthetic tests must verify direct API completion, primary failure followed
  by backup completion, and tool calling through the actual runtime.
- A model response does not prove app availability, delivery, or completion of
  a business task. Unknown write outcomes must use the existing durable workflow
  reconciliation; switching providers must never blindly resubmit them.

The default 2026.10.5 transient retry budget can wait through eight retries and
a 90-second outage window before fallback. A 45-second canary expired without
ever reaching the backup. An initial test with one retry completed in about 10 seconds. The final
configuration disables same-provider retries entirely and tests a synthetic 503
with a 120-second Retry-After hint to verify prompt fallback.
This measurement is a test result, not a production latency guarantee.

## Prepare and release

1. Merge the source changes and verify a clean, freshly fetched exact
   `origin/main` with `scripts/assert-release-ready.sh`. If GitHub cannot be
   reached, retain the candidates and stop before production deployment.
2. Stage the isolated runtime and inventory all per-profile CLI users, plugin
   SDK links, gateway owners, and state-format dependencies. Hotel currently has
   a system owner; do not enable its unused GUI plist. Keep a single supervisor.
3. Run the encrypted backup/restore workflow. Test migration on isolated state
   copies. Version 10.5 requires `agents.entries` instead of `agents.list` and
   moves legacy auth stores into agent databases. Restoring only a plist is
   insufficient rollback. Preserve model allowlists as `modelPolicy.allow`.
   Verify custom code reads both agent formats before migration. The profile
   examples retain the existing schema until this coordinated migration.
4. Provision an authorized OpenAI provider key in each target service's private
   environment as `OPENAI_FALLBACK_API_KEY`. Verify it with synthetic data. Do not
   copy another agent's app token, use subscription OAuth by accident, print
   keys, or replace tool-scoped credentials. No new key is automatically created.
5. Run `python3 scripts/prepare-openai-fallback.py --profile PROFILE` to inspect
   a content-free plan. Add `--output-dir /private/tmp/NEW-PRIVATE-DIRECTORY` to
   produce 0600 configuration/settings candidates outside source and live state.
   The directory must be new. It contains existing configuration secrets and
   must not be committed, uploaded, or included in ordinary reports.
6. In the isolated copy, migrate and validate the complete candidate with the
   new CLI, including all channel routes, sender allowlists, tool grants, native
   plugins and sessions. Preserve pending spools and task identities. Apply each
   `settings.candidate.json` to its matching isolated agent directory. Check
   tighter workspace-local session settings do not undo the retry bounds.
7. Test actual agent workflows with synthetic fixtures and fake external writes.
   Require unknown-outcome replay protection before adopting a provider switch
   after tool activity. Native plugin loading alone is insufficient.
8. Activate one profile at a time only after the coordinated runtime migration
   and rollback tests pass. Update that profile's schedulers and observer CLI
   together. Verify channel status, permitted tool use, deployed hashes, model
   completion and actual response delivery without unsolicited test messages.
   Correo remains disabled until its separate closure fix is deployed and tested.

The preparer rejects unknown profiles/agents, mixed agent schemas, conflicting
secret-provider definitions, custom OpenAI endpoints and request overrides. It
preserves routing, app secrets, auth profiles and tool policies. Existing user or
session model overrides can still be strict; audit those separately rather than
silently rewriting historical sessions.

## Cost

The official model page lists $0.75 per million input tokens, $0.075 cached input,
and $4.50 output. A 26,000-token uncached input plus 1,000 output tokens is about
$0.024 for one model call; a task may require several calls. Keep the model as
fallback and measure cost per completed business task. Local token counters do
not replace provider billing or an account/project budget.

Sources: [OpenAI model](https://developers.openai.com/api/docs/models/gpt-5.4-mini),
[OpenClaw release](https://docs.openclaw.ai/releases/2026.10.5),
[retry policy](https://docs.openclaw.ai/concepts/retry).

Run the reproducible canary with an isolated Node/CLI and a private output directory:

```sh
python3 scripts/uat-openai-fallback.py --node /isolated/node/bin/node \
  --openclaw-cli /isolated/openclaw/openclaw.mjs \
  --output-dir /private/tmp/openai-canary --mode failover
```

For `network-failover`, also pass `--deepseek-plugin` with the isolated official
provider package directory, not its compiled entry file. Its manifest supplies
the local model catalog; live discovery is unnecessary.

Modes `direct`, `failover`, `network-failover`, and `tools` use synthetic content
only. The network case supplies a fake credential to DeepSeek, so either an
unreachable endpoint or a rejected credential exercises fallback without
using a real DeepSeek account. The test succeeds only for an observed transport failure; an authentication rejection or unknown model is insufficient. OpenAI uses the explicitly supplied environment
key. Temporary agent state is removed, no channels are configured, and the tool
mode exposes one local fixture lookup. The process group has a 60-second outer
deadline; failure exits nonzero. Do not treat canaries as business-workflow UAT.

DeepSeek announced retirement of the two old IDs on July 24, 2026. The new
runtime rejects them before making a request. Current primary success remains
unverified while the Mac cannot reach DeepSeek; network-failover UAT must require
a transport failure, not merely model-not-found followed by OpenAI success.
See [retirement notice](https://api-docs.deepseek.com/news/news260424/) and
[current model IDs](https://api-docs.deepseek.com/quick_start/pricing/).

The Nómina direct-provider evaluation harness is updated to the current DeepSeek
primary. Its scripted fixtures validate financial gates; they do not establish
OpenAI conversation quality. Production backup acceptance still requires the
real OpenClaw pipeline with synthetic payroll data and human-review gates.
