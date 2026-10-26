import assert from "node:assert/strict";
import test from "node:test";
import { applyCuentaTopicPlan, planCuentaIntake } from "../../../scripts/configure-cuenta-intake.mjs";

function fixture() {
  return {
    agents: { list: [{ id: "cuenta" }, { id: "correo" }] },
    channels: { telegram: { allowFrom: [101], groupAllowFrom: ["101", "202"],
      groups: { "-200": { groupPolicy: "open", topics: {
        "2": { agentId: "cuenta", groupPolicy: "open", requireMention: false },
        "3": { agentId: "correo", groupPolicy: "open" },
      } } },
    } },
  };
}
const sessions = [{ sessionKey: "agent:cuenta:telegram:group:-200:topic:2", entry: { sessionId: "receipt-session", updatedAt: 100 } }];

test("repair targets only the receipt topic and deduplicates existing numeric senders", () => {
  const config = fixture();
  const original = structuredClone(config);
  const plan = planCuentaIntake(config, sessions);
  assert.deepEqual(plan.allowed, ["101", "202"]);
  assert.equal(plan.sessionChanged, true);
  assert.deepEqual(config, original, "planning is pure");
  applyCuentaTopicPlan(config, plan);
  original.channels.telegram.groups["-200"].topics["2"].groupPolicy = "allowlist";
  original.channels.telegram.groups["-200"].topics["2"].allowFrom = ["101", "202"];
  assert.deepEqual(config, original);
  const rerun = planCuentaIntake(config, [{ ...sessions[0], entry: { ...sessions[0].entry, queueMode: "followup" } }]);
  assert.equal(rerun.configChanged, false);
  assert.equal(rerun.sessionChanged, false);
  assert.ok(!JSON.stringify(plan.summary).includes("101"), "plan output excludes personal sender IDs");
});

test("ambiguous routes, missing sessions, and wildcard authorization fail closed", () => {
  const multiple = fixture();
  multiple.channels.telegram.groups["-200"].topics["4"] = { agentId: "cuenta" };
  assert.throws(() => planCuentaIntake(multiple, sessions), /exactly one configured Cuenta Telegram topic/);
  assert.throws(() => planCuentaIntake(fixture(), []), /existing Cuenta session/);
  const wildcard = fixture();
  wildcard.channels.telegram.allowFrom = ["*"];
  assert.throws(() => planCuentaIntake(wildcard, sessions), /verified numeric/);
});

test("repair refuses to silently remove additional topic senders or infer account scopes", () => {
  const additional = fixture();
  additional.channels.telegram.groups["-200"].topics["2"].allowFrom = ["303"];
  assert.throws(() => planCuentaIntake(additional, sessions), /additional sender IDs/);
  const accounts = fixture();
  accounts.channels.telegram.accounts = { other: {} };
  assert.throws(() => planCuentaIntake(accounts, sessions), /one Telegram account/);
});

test("keyed Cuenta identity repairs the same sender gate without adding retired fields", () => {
  const config = fixture();
  config.agents = { entries: { cuenta: { workspace: "/synthetic/receipts" }, correo: {} } };
  const identities = structuredClone(config.agents);
  const plan = planCuentaIntake(config, sessions);
  assert.deepEqual(plan.allowed, ["101", "202"]);
  applyCuentaTopicPlan(config, plan);
  assert.deepEqual(config.agents, identities);
  assert.deepEqual(config.channels.telegram.groups["-200"].topics["2"].allowFrom, ["101", "202"]);
  config.channels.telegram.allowFrom = ["*"];
  assert.throws(() => planCuentaIntake(config, sessions), /verified numeric/);
});

test("mixed or spoofed keyed identity never authorizes a Cuenta repair", () => {
  for (const agents of [
    { entries: { cuenta: {} }, list: [{ id: "cuenta" }] },
    { entries: { cuenta: { id: "correo" } } },
    { entries: { correo: { id: "cuenta" } } },
    { entries: { correo: {} } },
    { list: [{ id: "cuenta" }, { id: "cuenta" }] },
  ]) {
    const config = fixture();
    config.agents = agents;
    assert.throws(() => planCuentaIntake(config, sessions), /ambiguous|identity|exactly one/);
  }
});
