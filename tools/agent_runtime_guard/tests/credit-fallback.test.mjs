import test from "node:test";
import assert from "node:assert/strict";
import { createGuard } from "../guard.mjs";

const primary = "deepseek/deepseek-flash";
const fallback = "openai/gpt-5.4-mini";
const chain = { primary, fallbacks: [fallback] };
const context = { agentId: "cuenta", runId: "synthetic" };
const configured = model => ({ agents: { entries: { cuenta: { model } } } });

function fixture(config, provider = "deepseek") {
  const circuit = { provider, until: 1800000, code: "provider_credit" };
  const records = [];
  const guard = createGuard({ config, now: () => 1000, readCircuit: () => circuit,
    writeCircuit: () => assert.fail("gate must not clear or rewrite the circuit"), record: value => records.push(value) });
  return { guard, records, circuit };
}

test("configured cross-provider fallback reaches runtime gate before provider identity is known", () => {
  const config = configured(chain), original = JSON.stringify(config);
  const { guard, records, circuit } = fixture(config);
  assert.deepEqual(guard.beforeRun({}, context), { outcome: "pass" });
  assert.equal(guard.beforeTool({ toolName: "synthetic_mutation", params: {} }, context), undefined);
  assert.equal(records.at(-1).kind, "provider_credit_fallback_allowed");
  assert.equal(circuit.provider, "deepseek");
  assert.equal(circuit.until, 1800000);
  assert.equal(JSON.stringify(config), original);
});

test("known primary also permits runtime failover, while a selected other provider stays unaffected", () => {
  const { guard } = fixture(configured(chain));
  for (const provider of ["deepseek", "openai"]) {
    assert.equal(guard.beforeRun({}, { ...context, runId: provider, modelProviderId: provider }).outcome, "pass");
  }
});

test("same-provider fallbacks, aliases and absent configuration retain the credit stop", () => {
  for (const config of [undefined, configured(primary), configured({ primary, fallbacks: [] }),
    configured({ primary, fallbacks: ["deepseek/other"] }), configured({ primary, fallbacks: ["backup-alias"] })]) {
    assert.equal(fixture(config).guard.beforeRun({}, context).outcome, "block");
  }
});

test("only the selected agent's exact unambiguous configuration may unblock credit", () => {
  for (const config of [
    { agents: { entries: { other: { model: chain } } } },
    { agents: { list: [{ id: "cuenta", model: chain }, { id: "cuenta", model: primary }] } },
    { agents: { entries: { cuenta: { model: chain } }, list: [] } },
    { agents: { defaults: { model: chain } } },
  ]) assert.equal(fixture(config).guard.beforeRun({}, context).outcome, "block");
  assert.equal(fixture(configured(chain)).guard.beforeRun({}, { runId: "no-agent" }).outcome, "block");
  const legacy = { agents: { list: [{ id: "cuenta", model: chain }] } };
  assert.equal(fixture(legacy).guard.beforeRun({}, context).outcome, "pass");
});

test("default chain is inherited only when agent has no model; an explicit primary drops default fallbacks", () => {
  const defaults = { model: chain };
  assert.equal(fixture({ agents: { defaults, entries: { cuenta: {} } } }).guard.beforeRun({}, context).outcome, "pass");
  for (const model of [primary, { primary }, { primary, fallbacks: [] }]) {
    assert.equal(fixture({ agents: { defaults, entries: { cuenta: { model } } } }).guard.beforeRun({}, context).outcome, "block");
  }
  assert.equal(fixture({ agents: { defaults, entries: { cuenta: { model: { fallbacks: [fallback] } } } } }).guard.beforeRun({}, context).outcome, "pass");
});

test("explicit model policy cannot count a disallowed alternate", () => {
  const config = configured(chain);
  config.agents.defaults = { modelPolicy: { allow: [primary] } };
  assert.equal(fixture(config).guard.beforeRun({}, context).outcome, "block");
  config.agents.entries.cuenta.modelPolicy = { allow: [primary, "openai/*"] };
  assert.equal(fixture(config).guard.beforeRun({}, context).outcome, "pass");
  config.agents.entries.cuenta.modelPolicy = { allow: [] };
  assert.equal(fixture(config).guard.beforeRun({}, context).outcome, "pass");
  config.agents.entries.cuenta.modelPolicy = { allow: "invalid" };
  assert.equal(fixture(config).guard.beforeRun({}, context).outcome, "block");
});

test("unknown or malformed circuit attribution cannot assume an unaffected alternate", () => {
  for (const provider of ["unknown", "", null, "invalid provider"]) {
    assert.equal(fixture(configured(chain), provider).guard.beforeRun({}, context).outcome, "block");
  }
});

test("fallback permission never resets tool budgets or hides their terminal outcome", () => {
  const { guard, records } = fixture(configured(chain));
  for (let index = 0; index < 3; index++) guard.beforeTool({ toolName: "synthetic_read", params: {} }, context);
  assert.equal(guard.beforeTool({ toolName: "synthetic_read", params: {} }, context).block, true);
  assert.equal(guard.beforeRun({}, context).outcome, "pass");
  assert.equal(guard.beforeTool({ toolName: "synthetic_mutation", params: {} }, context).block, true);
  guard.end({ success: true }, context);
  assert.equal(records.at(-1).success, false);
  assert.equal(records.at(-1).code, "run_budget");
});
