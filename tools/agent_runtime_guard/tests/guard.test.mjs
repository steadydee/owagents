import test from "node:test";
import assert from "node:assert/strict";
import { createGuard, errorClass } from "../guard.mjs";
const ctx = { agentId: "cobros", runId: "one", modelProviderId: "deepseek" };

test("healthy runs explicitly pass the OpenClaw input gate", () => {
  assert.deepEqual(createGuard().beforeRun({}, ctx), { outcome: "pass" });
});

test("halts varying searches before a runaway loop and leaves next run usable", () => {
  const guard = createGuard();
  guard.beforeRun({}, ctx);
  for (let n = 0; n < 12; n++) assert.equal(guard.beforeTool({ toolName: "gmail_search", params: { q: n } }, ctx), undefined);
  assert.equal(guard.beforeTool({ toolName: "gmail_search", params: { q: 13 } }, ctx).block, true);
  assert.equal(guard.beforeTool({ toolName: "create_packet", params: {} }, ctx).block, true);
  guard.end({ success: true }, ctx);
  assert.equal(guard.beforeTool({ toolName: "gmail_search", params: {} }, { ...ctx, runId: "two" }), undefined);
});

test("canonical repeated read params cannot evade budget by ordering", () => {
  const guard = createGuard();
  for (let n = 0; n < 3; n++) assert.equal(guard.beforeTool({ toolName: "gmail_read", params: { a: 1, b: 2 } }, ctx), undefined);
  assert.equal(guard.beforeTool({ toolName: "gmail_read", params: { b: 2, a: 1 } }, ctx).block, true);
});

test("credit circuit survives restart and expires without blocking other providers", () => {
  let current = 0; let circuit = {}; const records = [];
  const options = { now: () => current, readCircuit: () => circuit, writeCircuit: value => { circuit = value; }, record: value => records.push(value) };
  createGuard(options).end({ success: false, error: "402 Insufficient Balance secret-value" }, ctx);
  const guard = createGuard(options);
  assert.equal(guard.beforeRun({}, ctx).outcome, "block");
  assert.equal(guard.beforeRun({}, { ...ctx, modelProviderId: "other" }).outcome, "pass");
  current = 1800001;
  assert.equal(guard.beforeRun({}, ctx).outcome, "pass");
  assert.equal(JSON.stringify(records).includes("secret-value"), false);
});

test("wall-time blocks new tools; attempt tokens remain retrospective telemetry", () => {
  let current = 0; const records = []; const guard = createGuard({ now: () => current, record: value => records.push(value) });
  guard.beforeRun({}, ctx); current = 600001;
  assert.equal(guard.beforeTool({ toolName: "tool", params: {} }, ctx).block, true);
  guard.end({ success: true }, ctx);
  assert.equal(records.at(-1).success, false);
  const other = { ...ctx, runId: "two" };
  guard.output({ usage: { total: 400001 } }, other);
  assert.equal(guard.beforeTool({ toolName: "tool", params: {} }, other), undefined);
  guard.end({ success: true }, other);
  assert.equal(records.at(-1).tokens, 400001);
  assert.equal(records.at(-1).success, true);
  assert.equal(errorClass("Context overflow"), "context_limit");
});

function lifecycle() {
  const records = []; let circuit = {}; let current = 1000;
  const guard = createGuard({ now: () => current, record: value => records.push(value), readCircuit: () => circuit, writeCircuit: value => { circuit = value; } });
  return { guard, records, circuit: () => circuit, advance: ms => { current += ms; } };
}
const errorAssistant = { role: "assistant", stopReason: "error", errorMessage: "402 Insufficient Balance private-content", content: [{ text: "private-reply" }] };
const successAssistant = { role: "assistant", stopReason: "stop", content: [{ text: "private-reply" }] };
function model(guard, id, fields = {}) {
  // Actual model hooks lack agentId, unlike agent_end and llm_output.
  guard.modelCall({ runId: ctx.runId, callId: id, provider: "deepseek", model: "deepseek-reasoner", outcome: "completed", ...fields }, { runId: ctx.runId });
}

test("installed SDK order joins model calls without agentId, end then output, and detects assistant errors", () => {
  const { guard, records, circuit } = lifecycle();
  guard.beforeRun({ prompt: "private-prompt" }, ctx);
  model(guard, "private-call-one");
  model(guard, "private-call-two", { outcome: "error", errorCategory: "billing", failureKind: "terminated" });
  guard.end({ success: true, messages: [errorAssistant], durationMs: 42 }, ctx);
  assert.equal(records.at(-1).code, "provider_credit");
  guard.output({ provider: "deepseek", usage: { input: 100, output: 20, cacheRead: 300, total: 420 }, lastAssistant: errorAssistant }, ctx);
  const final = records.at(-1);
  assert.equal(final.kind, "run_end");
  assert.equal(final.success, false);
  assert.equal(final.code, "provider_credit");
  assert.equal(final.tokens, 420);
  assert.equal(final.modelRequests, 2);
  assert.equal(final.modelErrors, 1);
  assert.equal(final.agent, "cobros");
  assert.equal(new Set(records.map(record => record.runKey)).size, 1);
  assert.equal(circuit().provider, "deepseek");
  assert.equal(JSON.stringify(records).includes("private"), false);
  assert.equal(records.filter(record => record.kind === "usage")[0].usageScope, "attempt");
});

test("late output corrects a provisional success without creating another run", () => {
  const { guard, records } = lifecycle();
  guard.beforeRun({}, ctx);
  guard.end({ success: true, messages: [] }, ctx);
  const provisional = records.at(-1);
  assert.equal(provisional.success, true);
  guard.output({ lastAssistant: errorAssistant, usage: { total: 12 } }, ctx);
  const final = records.at(-1);
  assert.equal(final.runKey, provisional.runKey);
  assert.equal(final.endedAt, provisional.endedAt);
  assert.equal(final.revision, provisional.revision + 1);
  assert.equal(final.success, false);
  assert.equal(final.tokens, 12);
  model(guard, "late-model-call");
  assert.equal(records.at(-1).modelRequests, 1);
  assert.equal(records.at(-1).success, false);
});

test("output before end has the same final error and usage", () => {
  const { guard, records } = lifecycle();
  guard.beforeRun({}, ctx);
  guard.output({ lastAssistant: errorAssistant, usage: { total: 17 } }, ctx);
  guard.end({ success: true, messages: [] }, ctx);
  assert.equal(records.at(-1).success, false);
  assert.equal(records.at(-1).code, "provider_credit");
  assert.equal(records.at(-1).tokens, 17);
});

test("recovered request errors remain request errors, with duplicate calls counted once", () => {
  const { guard, records } = lifecycle();
  guard.beforeRun({}, ctx);
  model(guard, "failed", { outcome: "error", failureKind: "connection_reset" });
  model(guard, "recovered");
  model(guard, "recovered");
  guard.end({ success: true, messages: [successAssistant] }, ctx);
  guard.output({ lastAssistant: successAssistant, usage: { total: 9 } }, ctx);
  assert.equal(records.at(-1).success, true);
  assert.equal(records.at(-1).modelRequests, 2);
  assert.equal(records.at(-1).modelErrors, 1);
  assert.equal(records.filter(record => record.kind === "model_call").length, 2);
});

test("new attempts sharing runId retain budgets, and recovered final outcome replaces previous failure", () => {
  const { guard, records } = lifecycle();
  guard.beforeRun({}, ctx);
  for (let n = 0; n < 10; n++) guard.beforeTool({ toolName: "gmail_search", params: { q: n } }, ctx);
  guard.end({ success: false, error: "Connection error" }, ctx);
  guard.output({ usage: { total: 5 } }, ctx);
  guard.beforeRun({}, ctx);
  guard.end({ success: true, messages: [successAssistant] }, ctx);
  guard.output({ lastAssistant: successAssistant, usage: { total: 7 } }, ctx);
  assert.equal(records.at(-1).success, true);
  assert.equal(records.at(-1).tokens, 12);
  assert.equal(records.at(-1).searches, 10);
  for (let n = 10; n < 12; n++) assert.equal(guard.beforeTool({ toolName: "gmail_search", params: { q: n } }, ctx), undefined);
  assert.equal(guard.beforeTool({ toolName: "gmail_search", params: { q: 12 } }, ctx).block, true);
});

test("cooldown denials do not prolong the circuit or leave a stale block after recovery", () => {
  const { guard, records, circuit, advance } = lifecycle();
  guard.beforeRun({}, ctx);
  guard.end({ success: true, messages: [errorAssistant] }, ctx);
  const until = circuit().until;
  advance(100000);
  assert.equal(guard.beforeRun({}, ctx).outcome, "block");
  guard.end({ success: false, error: "provider_credit" }, ctx);
  assert.equal(circuit().until, until);
  advance(1800000);
  assert.equal(guard.beforeRun({}, ctx).outcome, "pass");
  // A fresh recovered run is still subject to its original wall-time budget.
  guard.end({ success: true, messages: [successAssistant] }, ctx);
  assert.equal(records.at(-1).success, true);
});

test("late successful output cannot erase a terminal harness error", () => {
  const { guard, records } = lifecycle();
  guard.beforeRun({}, ctx);
  guard.end({ success: false, error: "context overflow" }, ctx);
  guard.output({ lastAssistant: successAssistant, usage: { total: NaN, input: -1 } }, ctx);
  assert.equal(records.at(-1).success, false);
  assert.equal(records.at(-1).code, "context_limit");
  assert.equal(records.at(-1).tokens, 0);
});
