import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { nativeHandlers, addReviewButtons, addReportButtons } from "./native-ui.mjs";

const token = "ABCDEF0123456789";
const ctx = { agentId: "nomina", sessionKey: "agent:nomina:telegram:direct:101", channel: "telegram",
  accountId: "default", senderId: "101", isAuthorizedSender: true, from: "telegram:101", to: "telegram:101", args: token };
const summary = "Revisión sintética con valores exactos.";
const sha256 = createHash("sha256").update(summary).digest("hex");
const receipt = { status: "sent", receipt: { platformMessageIds: ["100", "101"] } };
const prepared = { ok: true, result: {}, summary, sha256 };

test("buttons use typed native commands; never promote a bare si or confirmation from model text", () => {
  assert.equal(addReviewButtons({ text: "sí" }).presentation, undefined);
  assert.equal(addReviewButtons({ text: `/confirmar_nomina ${token}` }).presentation, undefined);
  assert.equal(addReviewButtons({ text: `/revisar_nomina ${token}` }).presentation.blocks[0].buttons[0].action.command, `/revisar_nomina ${token}`);
  assert.equal(addReviewButtons({ text: `/revisar_nomina ${token}\n/revisar_nomina 0000111122223333` }).presentation, undefined);
  assert.equal(addReportButtons({ text: "/informe_nomina run1 csv\n/informe_nomina run1 html" }).presentation.blocks[0].buttons.length, 2);
  assert.equal(addReportButtons({ text: "/informe_nomina ../secret csv" }).presentation, undefined);
});

test("full review delivery precedes recorded review and separate Confirmar button", async () => {
  const steps = [];
  const handlers = nativeHandlers({ workspace: "/unused", call: async (command, name, args, trusted) => {
    steps.push(command);
    assert.equal(trusted.source, "native_command");
    assert.equal(name, token);
    if (command === "review-delivered") assert.deepEqual(args, { sha256, message_ids: ["100", "101"] });
    return prepared;
  }, send: async (_ctx, trusted, payload, options) => {
    steps.push("send");
    assert.equal(trusted.chatId, "101");
    assert.equal(payload.presentation, undefined);
    options.onPayload(payload);
    return receipt;
  } });
  const result = await handlers.review(ctx);
  assert.deepEqual(steps, ["review", "send", "review-delivered"]);
  assert.equal(result.presentation.blocks[0].buttons[0].action.command, `/confirmar_nomina ${token}`);
});

for (const outcome of ["failed", "partial_failed", "suppressed", "throws", "missing_receipt"]) {
  test(`review ${outcome} cannot record review or offer confirmation`, async () => {
    const calls = [];
    const handlers = nativeHandlers({ workspace: "/unused", call: async command => { calls.push(command); return prepared; },
      send: async () => { if (outcome === "throws") throw new Error("PRIVATE"); return outcome === "missing_receipt" ? { status: "sent" } : { ...receipt, status: outcome }; } });
    const result = await handlers.review(ctx);
    assert.deepEqual(calls, ["review"]);
    assert.equal(result.presentation, undefined);
    assert.doesNotMatch(result.text, /PRIVATE/);
  });
}

test("invalid actor and missing references are clear and execute nothing", async () => {
  const handlers = nativeHandlers({ workspace: "/unused", call: async () => { throw new Error("Must not call"); }, send: async () => { throw new Error("Must not send"); } });
  for (const method of ["review", "confirm", "report"]) {
    assert.match((await handlers[method]({ ...ctx, isAuthorizedSender: false })).text, /no tiene acceso/);
  }
  assert.match((await handlers.review({ ...ctx, args: "" })).text, /Falta la referencia/);
  assert.match((await handlers.confirm({ ...ctx, args: "" })).text, /Primero pulsa Revisar/);
});

test("state change during delivery does not expose a confirmation control", async () => {
  const handlers = nativeHandlers({ workspace: "/unused", send: async (_ctx, _trusted, payload, options) => { options.onPayload(payload); return receipt; },
    call: async command => command === "review" ? prepared : { ok: false, error: { code: "STALE_PREVIEW" } } });
  const result = await handlers.review(ctx);
  assert.equal(result.presentation, undefined);
  assert.match(result.text, /datos cambiaron/);
});

test("confirmation enters only native approve and never a tool or transfer", async () => {
  const calls = [], events = [];
  const handlers = nativeHandlers({ workspace: "/unused", observe: (_trusted, stage, status) => { events.push([stage, status]); return true; },
    send: async (_ctx, _trusted, payload) => { assert.match(payload.text, /no se hizo transferencia/); return receipt; },
    call: async (...args) => { assert.deepEqual(events, [["confirmation", "pending"]]); calls.push(args); return { ok: true, summary: "Pago registrado; no se hizo transferencia." }; } });
  assert.equal((await handlers.confirm(ctx)).suppressReply, true);
  assert.equal(calls[0][0], "approve");
  assert.equal(calls[0][3].authorized, true);
  assert.deepEqual(events.at(-1), ["confirmation", "sent"]);
});

test("transformed review or missing normalization callback never enables approval", async () => {
  for (const transform of [true, false]) {
    const calls = [];
    const handlers = nativeHandlers({ workspace: "/unused", call: async command => { calls.push(command); return prepared; },
      send: async (_ctx, _trusted, _payload, options) => { if (transform) options.onPayload({ text: "Campo financiero eliminado" }); return receipt; } });
    assert.equal((await handlers.review(ctx)).presentation, undefined);
    assert.deepEqual(calls, ["review"]);
  }
});

test("lost confirmation response is journaled and approval is not repeated", async () => {
  const events = [];
  let calls = 0;
  const handlers = nativeHandlers({ workspace: "/unused", observe: (_trusted, stage, status) => { events.push([stage, status]); return true; },
    call: async () => { calls++; return { ok: true, summary: "Guardado" }; }, send: async () => { throw new Error("uncertain"); } });
  assert.match((await handlers.confirm(ctx)).text, /No he repetido/);
  assert.equal(calls, 1);
  assert.deepEqual(events.at(-1), ["confirmation", "uncertain"]);
});

test("confirmation cannot commit when durable pre-execution journal is unavailable", async () => {
  const handlers = nativeHandlers({ workspace: "/unused", observe: () => undefined,
    call: async () => { throw new Error("Must not approve"); }, send: async () => { throw new Error("Must not send"); } });
  assert.match((await handlers.confirm(ctx)).text, /No apliqué el cambio/);
});

test("private report path is server-selected and cannot escape workspace", async () => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "nomina-media-")));
  try {
    const directory = join(root, "exports", "a".repeat(64));
    mkdirSync(directory, { recursive: true });
    const path = join(directory, "payments.csv");
    writeFileSync(path, "synthetic");
    let mediaPath = path, sent = 0;
    const handlers = nativeHandlers({ workspace: root, call: async () => ({ ok: true, result: { media_path: mediaPath }, summary: "Informe privado" }),
      send: async (_ctx, _trusted, payload) => { sent++; assert.equal(payload.mediaUrl, path); return receipt; } });
    assert.equal((await handlers.report({ ...ctx, args: "run1 csv" })).suppressReply, true);
    mediaPath = join(root, "payroll-config.json");
    writeFileSync(mediaPath, "private");
    assert.match((await handlers.report({ ...ctx, args: "run1 csv" })).text, /no pasó/);
    assert.equal(sent, 1);
  } finally { rmSync(root, { force: true, recursive: true }); }
});
