import { createHash } from "node:crypto";

export function errorClass(error) {
  const text = String(error || "");
  if (/\b402\b|insufficient[ _-]balance|billing|credit.?quota|provider_credit/i.test(text)) return "provider_credit";
  if (/context.*(?:overflow|exceed|limit)|token.*budget|compaction.*fail/i.test(text)) return "context_limit";
  if (/timeout|timed out|first event/i.test(text)) return "provider_timeout";
  if (/connection|ENETUNREACH|ENOTFOUND|fetch failed|transport/i.test(text)) return "transport";
  if (/\b429\b|rate.?limit/i.test(text)) return "provider_rate_limit";
  if (/\b40[13]\b|unauthor|auth|forbidden/i.test(text)) return "provider_auth";
  return text ? "run_failed" : null;
}

const finiteCount = value => Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : 0;
const digest = value => createHash("sha256").update(value).digest("hex").slice(0, 24);
function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}
function terminalError(message) {
  if (!message || message.role !== "assistant" || !["error", "aborted"].includes(message.stopReason)) return null;
  return errorClass(message.errorMessage || message.errorCode || message.stopReason);
}
function hasAssistantText(message) {
  return message?.role === "assistant" && !terminalError(message) &&
    (typeof message.content === "string" ? Boolean(message.content.trim()) :
      Array.isArray(message.content) && message.content.some(part => part?.type === "text" && typeof part.text === "string" && part.text.trim()));
}

// Only literal provider/model references from trusted configuration count as an
// alternate. Alias resolution, auth eligibility and actual failover stay with
// OpenClaw; uncertainty here must retain the existing credit stop.
function configuredProviders(config, agentId) {
  const agents = config?.agents;
  if (!agents || !agentId || (Object.hasOwn(agents, "entries") && Object.hasOwn(agents, "list"))) return [];
  let agent;
  if (agents.entries && typeof agents.entries === "object" && !Array.isArray(agents.entries)) {
    if (Object.hasOwn(agents.entries, agentId)) agent = agents.entries[agentId];
  } else if (Array.isArray(agents.list)) {
    const matches = agents.list.filter(item => item?.id === agentId);
    if (matches.length === 1) agent = matches[0];
  }
  if (!agent || typeof agent !== "object" || Array.isArray(agent)) return [];
  const defaults = agents.defaults || {};
  const selected = agent.model ?? defaults.model;
  const defaultPrimary = typeof defaults.model === "string" ? defaults.model : defaults.model?.primary;
  let refs;
  if (typeof selected === "string") refs = [selected];
  else if (selected && typeof selected === "object" && !Array.isArray(selected)) {
    if (Object.hasOwn(selected, "fallbacks") && !Array.isArray(selected.fallbacks)) return [];
    // A per-agent primary (including a string) does not inherit default
    // fallbacks. An explicit [] also disables default fallbacks in OpenClaw.
    refs = [selected.primary ?? defaultPrimary, ...(selected.fallbacks || [])];
  } else return [];
  const policy = agent.modelPolicy && Object.hasOwn(agent.modelPolicy, "allow") ? agent.modelPolicy : defaults.modelPolicy;
  const allow = policy?.allow;
  if (allow !== undefined && !Array.isArray(allow)) return [];
  return [...new Set(refs.filter(ref => typeof ref === "string" && /^[a-z0-9][a-z0-9_-]*\/\S+$/.test(ref) &&
    (!allow?.length || allow.some(rule => typeof rule === "string" &&
      (rule === ref || (rule.endsWith("/*") && ref.startsWith(rule.slice(0, -1)))))))
    .map(ref => ref.slice(0, ref.indexOf("/"))))];
}

// Count only provider/tool-confirmed business records, never the model's prose.
export function verifiedBusinessOutcome(event) {
  if (event.error || event.result?.isError) return null;
  let result = event.result?.details?.structuredContent || event.result?.structuredContent;
  if (!result) {
    const text = event.result?.content?.find(part => part.type === "text")?.text;
    if (typeof text !== "string" || text.length > 1000000) return null;
    try { result = JSON.parse(text); } catch { return null; }
  }
  if (!result || result.ok !== true) return null;
  const types = {
    owlswatch_operations_create_expense_draft: ["expense_draft_created", result.expense_id || result.expenseId],
    owlswatch_quote_create_draft: ["quote_draft_created", result.quoteId || result.quote_id || result.id],
    owlswatch_cobros_create_gmail_draft: ["billing_draft_created", result.gmailDraftId],
    hotel_pms_create_reservation: ["reservation_created", result.reservation?.reservationId || result.reservation?.id],
    owlswatch_email_acknowledge_item: ["email_intake_processed", result.scanId && result.acknowledged ? `${result.scanId}:${result.acknowledged}` : null],
  };
  const match = types[event.toolName];
  if (!match || typeof match[1] !== "string" || !match[1] || match[1].length > 500) return null;
  return { outcome: match[0], taskKey: digest(`${match[0]}:${match[1]}`) };
}

export function createGuard({ config, now = Date.now, record = () => {}, readCircuit = () => ({}), writeCircuit = () => {} } = {}) {
  const runs = new Map();
  // model_call_ended has runId but no agentId in the installed runtime. Key
  // exclusively by trusted run identity, then learn agent/provider from hooks.
  const keyFor = (event, ctx) => event.runId || ctx.runId || ctx.sessionId || ctx.sessionKey || "unscoped";
  function getRun(event, ctx) {
    const key = keyFor(event, ctx);
    if (!runs.has(key)) {
      for (const [id, old] of runs) if (now() - old.updated > 3600000) runs.delete(id);
      if (runs.size >= 2000) runs.delete(runs.keys().next().value);
      runs.set(key, { runKey: digest(key), agent: "unknown", started: now(), updated: now(), calls: 0, searches: 0, tokens: 0,
        modelRequests: 0, modelErrors: 0, usageEvents: 0, responseCompleted: false, callIds: new Set(), repeats: new Map(), blocked: null, terminalCode: null, ended: null, revision: 0 });
    }
    const run = runs.get(key);
    run.updated = now();
    if (ctx.agentId) run.agent = ctx.agentId;
    if (event.provider || ctx.modelProviderId) run.provider = event.provider || ctx.modelProviderId;
    return run;
  }
  function emit(run, fields) { record({ runKey: run.runKey, agent: run.agent, ...fields }); }
  function circuitFor(code, run) {
    if (code === "provider_credit") writeCircuit({ provider: run.provider || "unknown", until: now() + 30 * 60000, code });
  }
  function snapshot(run) {
    if (!run.ended) return;
    const code = run.blocked || run.terminalCode || run.ended.code || (run.ended.success === true ? null : "run_failed");
    emit(run, { kind: "run_end", revision: ++run.revision, startedAt: new Date(run.started).toISOString(), endedAt: run.ended.at,
      success: run.ended.success === true && !code, code, durationMs: run.ended.durationMs ?? now() - run.started,
      responseCompleted: run.ended.success === true && !code && run.responseCompleted,
      calls: run.calls, searches: run.searches, tokens: run.tokens, modelRequests: run.modelRequests, modelErrors: run.modelErrors });
  }
  return {
    beforeRun(event, ctx) {
      const run = getRun(event, ctx);
      // A provider retry can reuse runId. Preserve tool budgets across attempts.
      if (run.ended) { run.ended = null; run.terminalCode = null; run.responseCompleted = false; }
      emit(run, { kind: "run_start", startedAt: new Date(run.started).toISOString() });
      const circuit = readCircuit();
      if (circuit.until > now() && (!run.provider || circuit.provider === "unknown" || circuit.provider === run.provider)) {
        const alternate = typeof circuit.provider === "string" && /^[a-z0-9][a-z0-9_-]*$/.test(circuit.provider) && circuit.provider !== "unknown" &&
          configuredProviders(config, run.agent).some(provider => provider !== circuit.provider);
        if (!alternate) {
          if (!run.blocked) run.blocked = "provider_credit";
          return { outcome: "block", category: "provider_credit", reason: "provider_credit_cooldown", message: "El proveedor de IA reportó saldo insuficiente. El trabajo queda pendiente; revise el saldo y vuelva a solicitarlo cuando se haya recuperado." };
        }
        // This is permission to attempt the configured chain, not evidence of
        // provider health. Do not clear the durable circuit or any run budget.
        emit(run, { kind: "provider_credit_fallback_allowed", provider: circuit.provider });
      }
      if (run.blocked === "provider_credit") run.blocked = null;
      // OpenClaw's gate treats undefined as an invalid/block decision.
      return { outcome: "pass" };
    },
    beforeTool(event, ctx) {
      const run = getRun(event, ctx);
      const name = event.toolName || ctx.toolName || "";
      const hash = digest(name + JSON.stringify(canonical(event.params || {})));
      const read = /(?:search|read|list|get|find)/i.test(name);
      run.calls += 1;
      if (/search/i.test(name)) run.searches += 1;
      run.repeats.set(hash, (run.repeats.get(hash) || 0) + 1);
      if (run.calls > 40 || run.searches > 12 || now() - run.started > 600000 || (read && run.repeats.get(hash) > 3)) {
        if (!run.blocked) emit(run, { kind: "budget_stop", code: "run_budget", calls: run.calls, searches: run.searches });
        run.blocked = "run_budget";
      }
      if (run.blocked) return { block: true, blockReason: "Run budget reached. Stop calling tools. Report completed work and the remaining item; never claim it succeeded. Resume remaining work in a new bounded run using durable state." };
    },
    modelCall(event, ctx) {
      const run = getRun(event, ctx);
      const callKey = digest(String(event.callId || `${run.runKey}:${run.modelRequests}`));
      if (run.callIds.has(callKey)) return;
      run.callIds.add(callKey);
      run.modelRequests += 1;
      const failed = event.outcome === "error";
      const code = failed ? errorClass([event.errorCategory, event.failureKind].filter(Boolean).join(" ") || "model_error") : null;
      if (failed) { run.modelErrors += 1; circuitFor(code, run); }
      emit(run, { kind: "model_call", callKey, provider: event.provider, model: event.model,
        success: !failed, code, durationMs: finiteCount(event.durationMs) });
      // Transport failures followed by a successful retry are request errors,
      // not necessarily a failed final run. Final status comes from the terminal
      // assistant/error metadata in end/output.
      snapshot(run);
    },
    afterTool(event, ctx) {
      const verified = verifiedBusinessOutcome(event);
      if (!verified) return;
      const run = getRun(event, ctx);
      emit(run, { kind: "business_outcome", ...verified });
    },
    output(event, ctx) {
      const run = getRun(event, ctx);
      if (hasAssistantText(event.lastAssistant) || event.assistantTexts?.some(text => typeof text === "string" && text.trim())) run.responseCompleted = true;
      const usage = event.usage || {};
      const input = finiteCount(usage.input), output = finiteCount(usage.output);
      const cacheRead = finiteCount(usage.cacheRead), cacheWrite = finiteCount(usage.cacheWrite);
      const tokens = usage.total === undefined ? input + output + cacheRead + cacheWrite : finiteCount(usage.total);
      run.tokens += tokens;
      emit(run, { kind: "usage", usageScope: "attempt", usageKey: digest(`${run.runKey}:${++run.usageEvents}`), provider: event.provider, model: event.model, input, output, cacheRead, cacheWrite, tokens });
      const code = terminalError(event.lastAssistant);
      if (code) { run.terminalCode = code; circuitFor(code, run); }
      else if (event.lastAssistant?.role === "assistant") run.terminalCode = null;
      // llm_output is attempt-level and may follow agent_end. Publish a revised
      // snapshot; consumers fold by runKey so there is still one final run.
      snapshot(run);
    },
    end(event, ctx) {
      const run = getRun(event, ctx);
      const lastAssistant = [...(event.messages || [])].reverse().find(message => message?.role === "assistant");
      if (hasAssistantText(lastAssistant)) run.responseCompleted = true;
      const code = terminalError(lastAssistant) || errorClass(event.error);
      if (code) {
        run.terminalCode = code;
        if (run.blocked !== "provider_credit") circuitFor(code, run);
      }
      run.ended = { success: event.success, code: errorClass(event.error), durationMs: event.durationMs, at: new Date(now()).toISOString() };
      snapshot(run);
      // Retain bounded metadata for late llm_output/model_call_ended hooks.
    },
  };
}
