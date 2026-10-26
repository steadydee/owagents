import test from "node:test";
import assert from "node:assert/strict";
import { createGuard, verifiedBusinessOutcome } from "../guard.mjs";

const result = value => ({ details: { structuredContent: value } });
test("business completion requires a positive result and durable provider identity", () => {
  const event = { toolName: "owlswatch_operations_create_expense_draft", result: result({ ok: true, expense_id: "fixture-id", vendor: "private text" }) };
  const outcome = verifiedBusinessOutcome(event);
  assert.equal(outcome.outcome, "expense_draft_created");
  assert.match(outcome.taskKey, /^[a-f0-9]{24}$/);
  assert.equal(JSON.stringify(outcome).includes("fixture-id"), false);
  assert.equal(verifiedBusinessOutcome({ ...event, error: "timeout" }), null);
  assert.equal(verifiedBusinessOutcome({ ...event, result: result({ ok: false, expense_id: "fixture-id" }) }), null);
  assert.equal(verifiedBusinessOutcome({ ...event, result: result({ ok: true }) }), null);
  assert.equal(verifiedBusinessOutcome({ ...event, toolName: "read" }), null);
});
test("failed handoff cannot become completed merely because a Gmail draft exists", () => {
  assert.equal(verifiedBusinessOutcome({ toolName: "owlswatch_cobros_create_gmail_draft", result: result({ ok: false, gmailDraftId: "fixture-id" }) }), null);
});
test("outcome journal contains hashes and categories only", () => {
  const records = [];
  const guard = createGuard({ record: entry => records.push(entry) });
  guard.afterTool({ toolName: "owlswatch_quote_create_draft", result: result({ ok: true, quoteId: "fixture-private", body: "confidential" }) }, { agentId: "cotiza", runId: "fixture-run" });
  assert.equal(records[0].kind, "business_outcome");
  assert.equal(records[0].agent, "cotiza");
  assert.equal(JSON.stringify(records).includes("private"), false);
  assert.equal(JSON.stringify(records).includes("confidential"), false);
});
test("successful harness termination alone is not an observed response", () => {
  const records = [];
  const guard = createGuard({ record: entry => records.push(entry) });
  const ctx = { agentId: "cuenta", runId: "fixture-run" };
  guard.beforeRun({}, ctx);
  guard.end({ success: true, messages: [] }, ctx);
  assert.equal(records.at(-1).responseCompleted, false);
  guard.output({ lastAssistant: { role: "assistant", content: [{ type: "text", text: "private reply" }] } }, ctx);
  assert.equal(records.at(-1).responseCompleted, true);
  assert.equal(JSON.stringify(records).includes("private reply"), false);
});
