import test from "node:test";
import assert from "node:assert/strict";
import { hostContext, confirmationReply } from "../approval-context.mjs";

const tool = { agentId: "hotel", requesterSenderId: "100", sessionKey: "agent:hotel:test", deliveryContext: { channel: "telegram", to: "telegram:group:-200:topic:3", threadId: "3" } };
const command = { agentId: "hotel", senderId: "100", sessionKey: "agent:hotel:test", channel: "telegram", from: "telegram:group:-200", messageThreadId: 3, isAuthorizedSender: true };

test("binds tool and native command to the same trusted sender and route", () => {
  const a = hostContext(tool), b = hostContext(command, { command: true });
  for (const key of ["chatId", "senderId", "threadId", "sessionKey", "accountId"]) assert.equal(a[key], b[key]);
  assert.equal(a.source, "tool_context");
  assert.equal(b.source, "native_command");
  assert.equal(b.authorized, true);
  assert.match(b.approvalEventId, /^[a-f0-9-]{36}$/);
});

test("fails closed on untrusted/missing/contradictory context", () => {
  for (const changed of [{ requesterSenderId: undefined }, { agentId: "main" }, { sessionKey: undefined }, { deliveryContext: { channel: "telegram", to: "-200:topic:3", threadId: 4 } }, { deliveryContext: { channel: "email", to: "-200" } }]) assert.equal(hostContext({ ...tool, ...changed }), null);
  assert.equal(hostContext({ ...command, isAuthorizedSender: false }, { command: true }), null);
  assert.equal(hostContext({ ...command, isAuthorizedSender: undefined }, { command: true }), null);
});

test("raw model parameters cannot supply sender identity or approval", () => {
  assert.equal(hostContext({ pendingId: "ABC", sourceMetadata: { telegramUserId: "100" }, confirmationText: "si", authorized: true }), null);
  assert.equal(hostContext(tool).authorized, undefined);
});

test("confirmation reply contains result and PMS link without payload details", () => {
  const result = confirmationReply({ ok: true, reservation: { pmsUrl: "https://example.invalid/reservations/test", preparedToken: "must-not-display", total: 100 } });
  assert.match(result, /Reserva creada/);
  assert.doesNotMatch(result, /must-not-display|100/);
  assert.equal(confirmationReply({ ok: false, error: { message: "Revisa PMS." } }), "Revisa PMS.");
});
