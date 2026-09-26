import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createCobrosGuards } from "../run-guards.mjs";
import { callPythonTool } from "../tool-bridge.mjs";
const workspace = fileURLToPath(new URL("../../../openclaw/agents/cobros", import.meta.url));
test("skill loads only for Cobros", () => {
  const guards = createCobrosGuards(workspace);
  assert.match(guards.prompt({}, { agentId: "cobros" }).appendSystemContext, /Do not call read/);
  assert.equal(guards.prompt({}, { agentId: "cuenta" }), undefined);
});
test("research budget survives prompt rebuild and isolates turns", () => {
  const guards = createCobrosGuards(workspace);
  const ctx = { agentId: "cobros", runId: "one" };
  const event = { toolName: "owlswatch_cobros_search_gmail_threads" };
  for (let i = 0; i < 3; i++) assert.equal(guards.beforeTool({ ...event, params: { query: String(i) } }, ctx), undefined);
  guards.prompt({}, ctx);
  assert.equal(guards.beforeTool(event, ctx).block, true);
  assert.equal(guards.beforeTool(event, { ...ctx, runId: "two" }), undefined);
  assert.equal(guards.beforeTool(event, { agentId: "cobros" }).block, true);
  for (let i = 0; i < 3; i++) assert.equal(guards.beforeTool({ toolName: "owlswatch_cobros_read_gmail_thread" }, ctx), undefined);
  assert.equal(guards.beforeTool({ toolName: "owlswatch_cobros_read_gmail_thread" }, ctx).block, true);
  assert.equal(guards.beforeTool({ toolName: "owlswatch_cobros_prepare" }, ctx), undefined);
});
test("bridge failure is bounded and write outcomes remain uncertain", async () => {
  const missing = await callPythonTool("/nonexistent/server.py", process.env, "search", {}, 2000);
  assert.equal(missing.ok, false);
  const timed = await callPythonTool(fileURLToPath(new URL("./slow_fixture.py", import.meta.url)), process.env, "create_packet", {}, 50);
  assert.equal(timed.error.code, "tool_timeout");
  assert.match(timed.error.message, /outcome is unconfirmed/);
});
