import test from "node:test";
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DeliveryJournal, deliverFinalReply } from "./delivery-journal.mjs";

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), "nomina-delivery-"));
  chmodSync(root, 0o700);
  writeFileSync(join(root, "payroll-config.json"), JSON.stringify({ enabled: true, schema_version: 1,
    telegram: { account_id: "default", allowed_sender_ids: ["101"], allowed_routes: [{ chat_id: "-201", thread_id: "" }] } }), { mode: 0o600 });
  let clock = 1000, journal = new DeliveryJournal(root, () => clock);
  t.after(() => { journal.close(); rmSync(root, { recursive: true, force: true }); });
  return { root, get journal() { return journal; }, tick: seconds => { clock += seconds; },
    restart: () => { journal.close(); journal = new DeliveryJournal(root, () => clock); } };
}
const incoming = { senderId: "101", messageId: "11", sessionKey: "agent:nomina:telegram:group:-201", content: "PRIVATE financial text" };
const ctx = { channelId: "telegram", accountId: "default", conversationId: "telegram:-201", messageId: "11", senderId: "101", sessionKey: incoming.sessionKey };
const payload = (runId, text) => ({ kind: "final", runId, channel: "telegram", sessionKey: incoming.sessionKey, payload: { text } });
const receipt = { status: "sent", receipt: { platformMessageIds: ["100"] } };

test("one durable row per inbound message survives restart without storing financial text", t => {
  const f = fixture(t);
  const id = f.journal.received(incoming, ctx);
  f.journal.received(incoming, ctx);
  f.restart();
  assert.equal(f.journal.db.prepare("SELECT count(*) AS n FROM deliveries").get().n, 1);
  assert.equal(f.journal.db.prepare("SELECT status FROM deliveries WHERE id=?").get(id).status, "received");
  assert.equal(readFileSync(join(f.root, "state/delivery.sqlite3")).includes(Buffer.from("PRIVATE financial text")), false);
});

test("sender, account, topic and group remain explicitly restricted", t => {
  const f = fixture(t);
  for (const [event, context] of [[{ ...incoming, senderId: "202" }, ctx], [incoming, { ...ctx, accountId: "other" }],
    [{ ...incoming, threadId: "5" }, ctx], [incoming, { ...ctx, conversationId: "telegram:-202" }]]) {
    assert.equal(f.journal.received(event, context), undefined);
  }
  assert.equal(f.journal.db.prepare("SELECT count(*) AS n FROM deliveries").get().n, 0);
});

test("generation success alone is not Telegram delivery; exact reply receipt marks delivery", t => {
  const f = fixture(t);
  const id = f.journal.received(incoming, ctx);
  f.journal.ended({ success: true, runId: "run-11" }, { agentId: "nomina" });
  assert.equal(f.journal.db.prepare("SELECT status FROM deliveries WHERE id=?").get(id).status, "received");
  const binding = f.journal.payload(payload("run-11", "Respuesta sintética"), ctx);
  f.journal.receipt(binding.id, receipt);
  assert.equal(f.journal.db.prepare("SELECT status FROM deliveries WHERE id=?").get(id).status, "delivered");
});

test("identical concurrent replies use distinct source-bound receipts", t => {
  const f = fixture(t);
  f.journal.received(incoming, ctx);
  f.journal.received({ ...incoming, messageId: "12", runId: "run-12" }, ctx);
  const first = f.journal.payload(payload("run-11", "Igual"), ctx);
  const second = f.journal.payload(payload("run-12", "Igual"), { ...ctx, messageId: "12" });
  f.journal.receipt(first.id, receipt);
  assert.equal(f.journal.db.prepare("SELECT status FROM delivery_payloads WHERE id=?").get(second.id).status, "reply_queued");
});

test("failed delivery and generation remain visible after restart", t => {
  const f = fixture(t);
  const id = f.journal.received(incoming, ctx);
  const binding = f.journal.payload(payload("run-11", "Respuesta"), ctx);
  f.journal.receipt(binding.id, { status: "failed", error: "SECRET ERROR" });
  f.restart();
  assert.equal(f.journal.db.prepare("SELECT status FROM deliveries").get().status, "delivery_unverified");
  assert.equal(readFileSync(join(f.root, "state/delivery.sqlite3")).includes(Buffer.from("SECRET ERROR")), false);
});

test("exact inbound identity binds late run and durable sender becomes sole owner", async t => {
  const f = fixture(t);
  f.journal.received(incoming, ctx);
  let calls = 0;
  const result = await deliverFinalReply(payload("late-run", "Respuesta"), ctx, f.journal, async (binding) => {
    calls++;
    assert.equal(binding.messageId, "11");
    assert.equal(binding.chatId, "-201");
    return receipt;
  });
  assert.equal(result.cancel, true);
  assert.equal(f.journal.db.prepare("SELECT run_id,status FROM deliveries").get().status, "delivered");
  assert.equal(f.journal.db.prepare("SELECT run_id FROM deliveries").get().run_id, "late-run");
  assert.equal((await deliverFinalReply(payload("late-run", "Respuesta"), ctx, f.journal, async () => { calls++; })).cancel, true);
  assert.equal(calls, 1);
});

test("durable recursion and unrelated topic cannot claim another inbound request", async t => {
  const f = fixture(t);
  const id = f.journal.received(incoming, ctx);
  const noSend = async () => { throw new Error("should not send"); };
  const plain = payload("run", "Igual");
  assert.deepEqual(await deliverFinalReply({ ...plain, runId: undefined }, ctx, f.journal, noSend), { payload: plain.payload });
  assert.deepEqual(await deliverFinalReply(plain, { ...ctx, conversationId: "telegram:-201:topic:6" }, f.journal, noSend), { payload: plain.payload });
  assert.equal(f.journal.db.prepare("SELECT status FROM deliveries WHERE id=?").get(id).status, "received");
});

test("unknown send suppresses a second core send and never replays on hook repeat", async t => {
  const f = fixture(t);
  f.journal.received(incoming, ctx);
  let calls = 0;
  const send = async () => { calls++; throw new Error("private exception"); };
  for (let i = 0; i < 2; i++) assert.equal((await deliverFinalReply(payload("run", "Respuesta"), ctx, f.journal, send)).cancel, true);
  assert.equal(calls, 1);
  assert.equal(f.journal.db.prepare("SELECT status FROM deliveries").get().status, "delivery_unverified");
});

test("distinct final payloads are all delivered and partial failure remains visible", async t => {
  const f = fixture(t);
  f.journal.received(incoming, ctx);
  let calls = 0;
  const send = async () => { calls++; return calls === 2 ? { status: "partial_failed" } : receipt; };
  for (const text of ["Primera parte", "Segunda parte", "Tercera parte", "Primera parte"])
    assert.equal((await deliverFinalReply(payload("run", text), ctx, f.journal, send)).cancel, true);
  assert.equal(calls, 3);
  assert.equal(f.journal.db.prepare("SELECT status FROM deliveries").get().status, "delivery_unverified");
});

test("metadata-only topic retains exact previously received thread", async t => {
  const f = fixture(t);
  f.journal.settings.telegram.allowed_routes.push({ chat_id: "-201", thread_id: "5" });
  f.journal.received({ ...incoming, threadId: "5" }, ctx);
  const result = await deliverFinalReply(payload("run", "Respuesta"), ctx, f.journal, async binding => {
    assert.equal(binding.threadId, "5");
    return receipt;
  });
  assert.equal(result.cancel, true);
  assert.equal(f.journal.db.prepare("SELECT count(*) AS n FROM deliveries").get().n, 1);
});

test("native report receipt is recorded without storing report data", t => {
  const f = fixture(t);
  f.journal.native({ accountId: "default", chatId: "-201", threadId: "", senderId: "101", sessionKey: incoming.sessionKey,
    approvalEventId: "event-1" }, "report", "sent");
  const row = f.journal.db.prepare("SELECT * FROM deliveries").get();
  assert.equal(row.kind, "native");
  assert.equal(row.status, "delivered");
});
