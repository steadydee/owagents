#!/usr/bin/env node
// Optional offline release check against the installed OpenClaw runtime.
// Usage: node scripts/verify-cuenta-telegram-gate.mjs /path/to/openclaw/package
// No bot credentials, network requests, messages, or live config changes.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const packageRoot = process.argv[2];
if (!packageRoot) throw new Error("Pass the installed OpenClaw package directory to this optional runtime check.");
const dist = resolve(packageRoot, "dist");

// Telegram's actual admission helpers are bundled internals, not a stable SDK
// subpath. Discover their named export mappings instead of pinning bundle hashes.
// Fail if a runtime upgrade removes/changes the helpers; never substitute a mock.
async function installedExport(prefix, name) {
  const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const mapping = new RegExp(`(?:[,{]\\s*)${escapedName}\\s+as\\s+(\\w+)`);
  for (const file of readdirSync(dist).filter((file) => file.startsWith(prefix) && file.endsWith(".js"))) {
    const path = resolve(dist, file);
    const source = readFileSync(path, "utf8");
    const exports = source.match(/export\s*\{[^}]+\}/g) ?? [];
    const match = exports.map((entry) => entry.match(mapping)).find(Boolean);
    if (!match) continue;
    const value = (await import(pathToFileURL(path)))[match[1]];
    if (typeof value === "function") return value;
  }
  throw new Error(`Installed OpenClaw no longer exposes ${name}; review the runtime admission contract.`);
}

const evaluateBaseAccess = await installedExport("group-access-", "evaluateTelegramGroupBaseAccess");
const normalizeAllowFrom = await installedExport("sent-message-cache-", "normalizeAllowFrom");
const resolveQueueSettings = await installedExport("queue-", "resolveQueueSettings$1");

const groupConfig = { groupPolicy: "open" };
const topicConfig = { groupPolicy: "allowlist", allowFrom: ["101"] };
const base = {
  isGroup: true,
  groupConfig,
  topicConfig,
  hasGroupAllowOverride: true,
  effectiveGroupAllow: normalizeAllowFrom(topicConfig.allowFrom),
  enforceAllowOverride: true,
  requireSenderForAllowOverride: true,
};
assert.equal(evaluateBaseAccess({ ...base, senderId: "101", senderUsername: "DifferentName" }).allowed, true);
assert.deepEqual(evaluateBaseAccess({ ...base, senderId: "303", senderUsername: "Dennis" }), {
  allowed: false, reason: "group-override-unauthorized",
});
assert.equal(evaluateBaseAccess({ ...base, senderId: "" }).allowed, false);
assert.equal(evaluateBaseAccess({ ...base, topicConfig: { ...topicConfig, groupPolicy: "open" }, senderId: "303" }).allowed, false,
  "an explicit topic sender override still gates an open-policy topic");
assert.equal(evaluateBaseAccess({ ...base, topicConfig: { groupPolicy: "open" }, hasGroupAllowOverride: false, senderId: "303" }).allowed, true,
  "open topics without a sender override admit non-allowlisted users");

assert.equal(resolveQueueSettings({ cfg: {}, channel: "telegram", sessionEntry: {} }).mode, "steer");
assert.equal(resolveQueueSettings({ cfg: {}, channel: "telegram", sessionEntry: { queueMode: "followup" } }).mode, "followup");
assert.equal(resolveQueueSettings({ cfg: { messages: { queue: { mode: "steer" } } }, channel: "telegram", sessionEntry: { queueMode: "followup" } }).mode, "followup");

const version = JSON.parse(readFileSync(resolve(packageRoot, "package.json"), "utf8")).version;
console.log(`OpenClaw ${version}: Receipts topic sender gate and session followup override verified offline.`);
