import test from "node:test";
import assert from "node:assert/strict";
import { hostContext, confirmationToken, confirmationReply, reviewReply } from "./approval-context.mjs";

const tool = { agentId: "nomina", sessionKey: "agent:nomina:telegram:group:-900:topic:8",
  messageChannel: "telegram", agentAccountId: "default", requesterSenderId: "101",
  deliveryContext: { channel: "telegram", accountId: "default", to: "telegram:group:-900:topic:8", threadId: 8 } };
const command = { agentId: "nomina", sessionKey: tool.sessionKey, channel: "telegram", accountId: "default",
  senderId: "101", isAuthorizedSender: true, from: "telegram:group:-900:topic:8", to: "telegram:-900", messageThreadId: 8 };

test("actual SDK Telegram tool and command contexts produce the same binding", () => {
  const pending = hostContext(tool);
  const approved = hostContext(command, { command: true, now: 100000 });
  for (const key of ["agentId", "channel", "senderId", "accountId", "chatId", "threadId", "sessionKey"]) assert.equal(pending[key], approved[key]);
  assert.equal(pending.source, "tool_context");
  assert.equal(approved.source, "native_command");
  assert.equal(approved.authorized, true);
  assert.equal(approved.approvedAt, 100);
  assert.match(approved.approvalEventId, /^[\da-f-]{36}$/);
  assert.notEqual(hostContext(command, { command: true }).approvalEventId, approved.approvalEventId);
  assert.equal(pending.authorized, undefined);
});

test("missing or conflicting host identity and routing fail closed", () => {
  for (const patch of [{ agentId: "main" }, { agentId: undefined }, { requesterSenderId: undefined },
    { requesterSenderId: "user-name" }, { sessionKey: undefined }, { sessionKey: "x\n" },
    { messageChannel: "webchat" }, { agentAccountId: "another" },
    { deliveryContext: { ...tool.deliveryContext, threadId: 9 } },
    { deliveryContext: { ...tool.deliveryContext, threadId: "" } },
    { deliveryContext: { ...tool.deliveryContext, to: "telegram:group:staff" } },
    { deliveryContext: { ...tool.deliveryContext, to: "telegram:0" } },
  ]) assert.equal(hostContext({ ...tool, ...patch }), null, JSON.stringify(patch));
  assert.equal(hostContext({ ...tool, deliveryContext: { ...tool.deliveryContext, accountId: undefined }, agentAccountId: undefined }), null);
});

test("command requires host authorization, matching agent, route and topic", () => {
  for (const patch of [{ isAuthorizedSender: false }, { isAuthorizedSender: "true" },
    { isAuthorizedSender: undefined }, { senderId: "" }, { agentId: "hotel" }, { sessionKey: undefined },
    { accountId: undefined }, { channelId: "slack" }, { messageThreadId: 99 },
    { to: "telegram:-901" }, { to: "telegram:group:-900:topic:99" }, { to: "invalid" },
  ]) assert.equal(hostContext({ ...command, ...patch }, { command: true }), null, JSON.stringify(patch));
});

test("private chat supports SDK numeric IDs and explicit default account", () => {
  const result = hostContext({ ...command, sessionKey: "agent:nomina:telegram:direct:101", from: "telegram:101", to: "telegram:101", messageThreadId: undefined }, { command: true });
  assert.equal(result.chatId, "101");
  assert.equal(result.threadId, "");
});

test("approval token accepts only exact displayed hexadecimal token", () => {
  assert.equal(confirmationToken(" ABCDEF0123456789 "), "ABCDEF0123456789");
  for (const value of ["abcdef0123456789", "ABCDEF0123456789 approve", "ABCDEF0123456789\nXYZ", "A".repeat(33), "a", null, {}, "G".repeat(16)]) assert.equal(confirmationToken(value), null);
});

test("native replies never serialize internal result records", () => {
  assert.equal(confirmationReply({ ok: true, summary: "Nómina finalizada.", result: { bank_account: "do-not-display" } }), "Nómina finalizada.");
  assert.ok(!confirmationReply({ ok: true, result: { bank_account: "do-not-display" } }).includes("do-not-display"));
  assert.equal(confirmationReply({ ok: false, error: { message: "Esta revisión venció." } }), "Esta revisión venció.");
  assert.ok(confirmationReply({ ok: true, summary: "x".repeat(1300) }).length < 1200);
});

test("native review preserves complete deterministic previews and fails visibly if unavailable", () => {
  const preview = "Persona sintética COP 125\n".repeat(200);
  assert.equal(reviewReply({ ok: true, summary: preview }), preview);
  assert.match(reviewReply({ ok: true }), /No confirmes/);
  assert.match(reviewReply({ ok: true, summary: "x".repeat(60001) }), /No confirmes/);
});
