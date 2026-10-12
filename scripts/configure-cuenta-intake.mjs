#!/usr/bin/env node
// Plans by default. Uses only public OpenClaw SDK mutation/session APIs.
import { accessSync, constants, readFileSync, realpathSync } from "node:fs";
import { dirname, delimiter, isAbsolute, resolve } from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

function numericIds(entries) {
  if (entries === undefined) return [];
  if (!Array.isArray(entries)) throw new Error("Sender allowlists must be arrays.");
  return entries.map((value) => {
    if ((typeof value !== "number" && typeof value !== "string") ||
        (typeof value === "number" && !Number.isSafeInteger(value)) || !/^[1-9]\d{0,19}$/.test(String(value))) {
      throw new Error("Sender allowlists must contain verified numeric user IDs only.");
    }
    return String(value);
  });
}

export function planCuentaIntake(config, sessions) {
  const telegram = config.channels?.telegram;
  if (!telegram || Object.keys(telegram.accounts ?? {}).length) {
    throw new Error("This repair requires one Telegram account configured at the profile level.");
  }
  if (config.agents?.list?.filter((agent) => agent.id === "cuenta").length !== 1) {
    throw new Error("Expected exactly one configured Cuenta agent.");
  }
  const routes = Object.entries(telegram.groups ?? {}).flatMap(([groupId, group]) =>
    Object.entries(group.topics ?? {}).filter(([, topic]) => topic.agentId === "cuenta")
      .map(([topicId, topic]) => ({ groupId, topicId, topic })));
  if (routes.length !== 1) throw new Error("Expected exactly one configured Cuenta Telegram topic.");
  const { groupId, topicId, topic } = routes[0];
  if (!/^-\d+$/.test(groupId) || !/^\d+$/.test(topicId)) throw new Error("Cuenta route IDs must be numeric.");
  const allowed = [...new Set([...numericIds(telegram.allowFrom), ...numericIds(telegram.groupAllowFrom)])].sort();
  if (!allowed.length) throw new Error("No verified numeric Telegram senders are configured.");
  if (numericIds(topic.allowFrom).some((id) => !allowed.includes(id))) {
    throw new Error("The existing Cuenta topic contains additional sender IDs; review before changing it.");
  }
  const sessionKey = `agent:cuenta:telegram:group:${groupId}:topic:${topicId}`;
  const matchingSessions = sessions.filter((item) => item.sessionKey === sessionKey);
  if (matchingSessions.length !== 1 || !matchingSessions[0].entry?.sessionId) {
    throw new Error("Expected one existing Cuenta session for the configured receipt topic.");
  }
  const entry = matchingSessions[0].entry;
  const configChanged = topic.groupPolicy !== "allowlist" ||
    JSON.stringify(numericIds(topic.allowFrom).sort()) !== JSON.stringify(allowed);
  return {
    groupId, topicId, allowed, sessionKey, sessionId: entry.sessionId,
    configChanged, sessionChanged: entry.queueMode !== "followup",
    summary: {
      topic: "Cuenta receipt topic",
      groupPolicy: { before: topic.groupPolicy ?? "inherited", after: "allowlist" },
      allowFrom: { beforeCount: topic.allowFrom?.length ?? 0, afterCount: allowed.length, source: "existing numeric profile sender IDs" },
      queueMode: { before: entry.queueMode ?? "inherited", after: "followup" },
      configChanged,
      sessionChanged: entry.queueMode !== "followup",
    },
  };
}

export function applyCuentaTopicPlan(config, plan) {
  const topic = config.channels.telegram.groups[plan.groupId].topics[plan.topicId];
  if (topic.agentId !== "cuenta") throw new Error("Cuenta route changed; create a new plan.");
  topic.groupPolicy = "allowlist";
  topic.allowFrom = [...plan.allowed];
}

function installedPackageRoot() {
  for (const directory of (process.env.PATH ?? "").split(delimiter)) {
    if (!directory) continue;
    const candidate = resolve(directory, "openclaw");
    try {
      accessSync(candidate, constants.X_OK);
      const packageRoot = dirname(realpathSync(candidate));
      if (JSON.parse(readFileSync(resolve(packageRoot, "package.json"), "utf8")).name === "openclaw") return packageRoot;
    } catch { /* Try the next executable on PATH. */ }
  }
  throw new Error("Cannot resolve the installed OpenClaw executable; pass --runtime-root.");
}

async function main(argv) {
  let configPath, runtimeRoot, apply = false;
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--config") configPath = argv[++i];
    else if (argv[i] === "--runtime-root") runtimeRoot = argv[++i];
    else if (argv[i] === "--apply") apply = true;
    else throw new Error("Usage: configure-cuenta-intake.mjs --config /absolute/openclaw.json [--runtime-root /installed/openclaw] [--apply]");
  }
  if (!configPath || !isAbsolute(configPath)) throw new Error("Pass the explicit absolute profile config path with --config.");
  process.env.OPENCLAW_CONFIG_PATH = resolve(configPath);
  process.env.OPENCLAW_STATE_DIR = dirname(resolve(configPath));
  const runtimeRequire = createRequire(resolve(runtimeRoot ?? installedPackageRoot(), "package.json"));
  const sdk = await import(pathToFileURL(runtimeRequire.resolve("openclaw/plugin-sdk/config-runtime")));
  const { OpenClawSchema } = await import(pathToFileURL(runtimeRequire.resolve("openclaw/plugin-sdk/config-schema")));
  const { snapshot } = await sdk.readConfigFileSnapshotForWrite();
  if (!snapshot.valid || !snapshot.exists || resolve(snapshot.path) !== resolve(configPath) || !snapshot.hash) {
    throw new Error("The selected profile config must exist, validate, and provide a write fingerprint.");
  }
  const source = snapshot.sourceConfig;
  const storePath = sdk.resolveStorePath(source.session?.store, { agentId: "cuenta", env: process.env });
  const scope = { agentId: "cuenta", storePath, readConsistency: "latest" };
  const plan = planCuentaIntake(source, sdk.listSessionEntries(scope));
  const proposed = structuredClone(source);
  applyCuentaTopicPlan(proposed, plan);
  if (!OpenClawSchema.safeParse(proposed).success) throw new Error("Proposed profile config does not pass the installed OpenClaw schema.");
  console.log(JSON.stringify({ mode: apply ? "apply" : "plan", ...plan.summary }, null, 2));
  if (!apply) return;

  if (plan.configChanged) {
    await sdk.mutateConfigFile({
      base: "source", baseHash: snapshot.hash,
      afterWrite: { mode: "none", reason: "Cuenta intake release performs a controlled gateway reload after verification." },
      mutate(draft) {
        applyCuentaTopicPlan(draft, plan);
        if (!OpenClawSchema.safeParse(draft).success) throw new Error("Proposed profile config failed validation; nothing was written.");
      },
    });
  }
  if (plan.sessionChanged) {
    const updated = await sdk.patchSessionEntry({
      ...scope, sessionKey: plan.sessionKey, preserveActivity: true,
      // This repair must not prune any sessions or transcripts as a side effect.
      maintenanceConfig: { mode: "warn", pruneAfterMs: Number.MAX_SAFE_INTEGER,
        modelRunPruneAfterMs: Number.MAX_SAFE_INTEGER, maxEntries: Number.MAX_SAFE_INTEGER,
        resetArchiveRetentionMs: null, maxDiskBytes: null, highWaterBytes: null },
      update(entry) {
        if (entry.sessionId !== plan.sessionId) throw new Error("Cuenta session changed during repair; run a fresh plan.");
        return { queueMode: "followup" };
      },
    });
    if (updated?.queueMode !== "followup") throw new Error("Cuenta queue update was not confirmed; configuration may already be restricted. Rerun the plan.");
  }
  const { snapshot: verified } = await sdk.readConfigFileSnapshotForWrite();
  const remaining = planCuentaIntake(verified.sourceConfig, sdk.listSessionEntries(scope));
  if (remaining.configChanged || remaining.sessionChanged) throw new Error("Post-write verification failed; rerun the plan.");
  console.log("Verified: only the Cuenta receipt-topic sender gate and existing session queue mode were targeted. Reload the gateway after release verification.");
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).catch((error) => {
    // Do not print SDK error objects, config snapshots, or credential-bearing values.
    console.error(error instanceof Error ? error.message : "Cuenta intake configuration failed.");
    process.exitCode = 1;
  });
}
