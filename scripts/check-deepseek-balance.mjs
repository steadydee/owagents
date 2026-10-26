#!/usr/bin/env node
// Credentials stay inside the explicitly selected runtime's auth resolver.
import { readFileSync, realpathSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import { homedir } from "node:os";
import { pathToFileURL } from "node:url";

const PROFILES = ["owlswatch", "hotel", "bailey-finance", "nomina"];

export function configuredAgents(cfg) {
  const section = cfg.agents ?? {};
  if (!section || typeof section !== "object" || Array.isArray(section)) throw new Error("invalid_agents");
  if (Object.hasOwn(section, "entries") && Object.hasOwn(section, "list")) throw new Error("ambiguous_agents");
  let entries;
  if (Object.hasOwn(section, "entries")) {
    if (!section.entries || typeof section.entries !== "object" || Array.isArray(section.entries)) throw new Error("invalid_entries");
    entries = Object.entries(section.entries);
  } else {
    if (!Array.isArray(section.list ?? [])) throw new Error("invalid_list");
    entries = (section.list ?? []).map(agent => [agent?.id, agent]);
  }
  const seen = new Set();
  for (const [id, agent] of entries) {
    if (typeof id !== "string" || !/^[a-z0-9][a-z0-9_-]{0,63}$/i.test(id) || seen.has(id)
        || !agent || typeof agent !== "object" || Array.isArray(agent)
        || (Object.hasOwn(agent, "id") && agent.id !== id)) throw new Error("invalid_identity");
    seen.add(id);
  }
  return entries;
}

export async function checkBalance(cfg, { state, home, resolveAuth, fetchBalance = globalThis.fetch }) {
  const expand = value => typeof value === "string" ? value.replace(/^~\//, home + "/") : undefined;
  let apiKey;
  for (const [id, agent] of configuredAgents(cfg)) {
    try {
      const auth = await resolveAuth({ provider: "deepseek", cfg,
        agentDir: expand(agent.agentDir) || join(state, "agents", id, "agent"),
        workspaceDir: expand(agent.workspace ?? cfg.agents?.defaults?.workspace) });
      if (auth.mode === "api-key" && typeof auth.apiKey === "string" && auth.apiKey) { apiKey = auth.apiKey; break; }
    } catch { /* A later configured specialist may own the existing credential. */ }
  }
  if (!apiKey) return { status: "credential_unavailable" };
  const response = await fetchBalance("https://api.deepseek.com/user/balance", {
    headers: { Authorization: `Bearer ${apiKey}` }, signal: AbortSignal.timeout(10000), redirect: "error",
  });
  if (!response.ok) return { status: "provider_error", httpStatus: response.status };
  const raw = await response.text();
  if (raw.length > 65536) throw new Error("oversize");
  const data = JSON.parse(raw);
  const balances = (Array.isArray(data.balance_infos) ? data.balance_infos : [])
    .filter(value => ["USD", "CNY"].includes(value.currency) && typeof value.total_balance === "string"
      && /^\d+(?:\.\d+)?$/.test(value.total_balance) && Number.isFinite(Number(value.total_balance)))
    .map(value => ({ currency: value.currency, total: Number(value.total_balance) }));
  if (typeof data.is_available !== "boolean" || !balances.length) throw new Error("invalid_contract");
  return { status: "ok", available: data.is_available, balances };
}

async function main() {
  const [profile, flag, sdk, ...extra] = process.argv.slice(2);
  const emit = value => process.stdout.write(JSON.stringify(value) + "\n");
  if (!PROFILES.includes(profile)) { emit({ status: "invalid_profile" }); process.exitCode = 2; return; }
  if (flag !== "--sdk-root" || !sdk || !isAbsolute(sdk) || extra.length) {
    emit({ status: "runtime_unconfigured" }); return;
  }
  const state = join(homedir(), `.openclaw-${profile}`);
  process.env.OPENCLAW_PROFILE = profile;
  process.env.OPENCLAW_STATE_DIR = state;
  process.env.OPENCLAW_CONFIG_PATH = join(state, "openclaw.json");
  // SDK diagnostic output is not part of this content-free interface.
  for (const name of ["log", "info", "warn", "debug", "error"]) console[name] = () => {};
  try {
    const root = realpathSync(sdk);
    if (JSON.parse(readFileSync(join(root, "package.json"), "utf8")).name !== "openclaw") throw new Error("invalid_runtime");
    const { resolveApiKeyForProvider } = await import(pathToFileURL(join(root, "dist/plugin-sdk/provider-auth-runtime.js")).href);
    const cfg = JSON.parse(readFileSync(process.env.OPENCLAW_CONFIG_PATH, "utf8"));
    emit(await checkBalance(cfg, { state, home: homedir(), resolveAuth: resolveApiKeyForProvider }));
  } catch {
    emit({ status: "check_failed" });
  }
}

if (process.argv[1] && pathToFileURL(realpathSync(process.argv[1])).href === import.meta.url) await main();
