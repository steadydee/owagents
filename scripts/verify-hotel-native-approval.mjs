#!/usr/bin/env node
// Actual installed command dispatcher -> plugin -> Python -> fake PMS transport.
// No bot, PMS credentials, real reservations, Telegram delivery or network I/O.
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, copyFileSync, readFileSync, writeFileSync, symlinkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const packageRoot = process.argv[2];
if (!packageRoot) throw new Error("Pass the installed OpenClaw package directory.");
const sourceRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const runtime = await import(pathToFileURL(resolve(packageRoot, "dist/plugin-sdk/plugin-runtime.js")));
const temp = mkdtempSync(join(tmpdir(), "hotel-native-uat-"));
const pluginDir = join(temp, "plugin");
mkdirSync(join(pluginDir, "node_modules"), { recursive: true });
for (const file of ["openclaw-plugin.js", "approval-context.mjs"]) copyFileSync(join(sourceRoot, "tools/hotel_pms", file), join(pluginDir, file));
writeFileSync(join(pluginDir, "package.json"), JSON.stringify({ type: "module" }));
symlinkSync(resolve(packageRoot), join(pluginDir, "node_modules/openclaw"));
Object.assign(process.env, { HOTEL_PMS_SERVER: join(sourceRoot, "tools/hotel_pms/tests/native_fixture.py"),
  HOTEL_TEST_SERVER_DIR: join(sourceRoot, "tools/hotel_pms"), HOTEL_PMS_WORKSPACE: temp, OPENCLAW_CONFIG_PATH: join(temp, "no-live-config.json") });

try {
  const tools = new Map();
  const plugin = (await import(pathToFileURL(join(pluginDir, "openclaw-plugin.js")))).default;
  plugin.register({ registerTool(factory, { name }) { tools.set(name, factory); }, registerCommand(command) {
    const result = runtime.registerPluginCommand("hotel-pms", command, { pluginRoot: pluginDir });
    assert.equal(result.ok, true);
  } });
  const toolContext = { agentId: "hotel", requesterSenderId: "101", sessionKey: "agent:hotel:telegram:group:-202:topic:3", deliveryContext: { channel: "telegram", to: "telegram:group:-202:topic:3", threadId: "3", accountId: "default" } };
  const commandContext = { senderId: "101", channel: "telegram", isAuthorizedSender: true, agentId: "hotel", sessionKey: toolContext.sessionKey,
    from: "telegram:group:-202", to: "telegram:-202", accountId: "default", messageThreadId: 3,
    config: { agents: { list: [{ id: "hotel" }] }, channels: { telegram: { allowFrom: ["101"], groups: { "-202": { topics: { "3": { agentId: "hotel" } } } } } } } };
  async function callTool(name, args) {
    return JSON.parse((await tools.get(name)(toolContext).execute("synthetic-call", args)).content[0].text);
  }
  async function prepare(guestName) {
    const result = await callTool("hotel_pms_prepare_reservation", { bookingType: "overnight_stay", guestName, arrivalDate: "2028-06-21", departureDate: "2028-06-22", adultsCount: 2, unitAllocations: [{ unitCode: "cabin", quantity: 1 }], source: "direct" });
    assert.equal(result.prepare?.status, "ready", JSON.stringify(result));
    return result.prepare.pendingId;
  }
  async function confirm(id, changed = {}) {
    const commandBody = `/confirmar_reserva ${id}`;
    const matched = runtime.matchPluginCommand(commandBody, { channel: "telegram" });
    assert.ok(matched);
    return runtime.executePluginCommand({ ...commandContext, ...changed, ...matched, commandBody });
  }
  function calls() {
    try { return readFileSync(join(temp, "provider-calls.jsonl"), "utf8").trim().split("\n").map(JSON.parse); }
    catch (error) { if (error.code === "ENOENT") return []; throw error; }
  }
  const id = await prepare("Synthetic valid approval");
  const forged = await callTool("hotel_pms_create_reservation", { pendingId: id, confirmationText: "si", authorized: true });
  assert.equal(forged.error.code, "human_approval_required");
  assert.match((await confirm(id, { isAuthorizedSender: false })).text, /authorization/);
  for (const changed of [{ senderId: "303" }, { messageThreadId: 4 }, { sessionKey: "agent:hotel:other" }, { accountId: "other" }]) {
    assert.doesNotMatch((await confirm(id, changed)).text, /Reserva creada/);
  }
  assert.equal(calls().length, 0);
  assert.match((await confirm(id)).text, /Reserva creada en PMS/);
  assert.match((await confirm(id)).text, /Reserva creada en PMS/);
  assert.equal(calls().length, 1);
  assert.equal(calls()[0].actor.telegramUserId, "101");
  assert.ok(calls()[0].actor.confirmationEventId);
  const lostId = await prepare("Synthetic lost response");
  writeFileSync(join(temp, "lose-provider-response"), "1");
  assert.doesNotMatch((await confirm(lostId)).text, /Reserva creada/);
  assert.doesNotMatch((await confirm(lostId)).text, /Reserva creada/);
  assert.equal(calls().length, 2, "Uncertain write must not be replayed");
  console.log(`OpenClaw ${JSON.parse(readFileSync(resolve(packageRoot, "package.json"))).version}: native Hotel auth, binding, one-write confirmation and uncertain-outcome replay checks passed offline.`);
} finally {
  runtime.clearPluginCommandsForPlugin("hotel-pms");
  rmSync(temp, { recursive: true, force: true });
}
