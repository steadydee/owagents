#!/usr/bin/env node
// Credentials stay inside OpenClaw's normal auth resolver and this process.
import { execFileSync } from "node:child_process";
import { readFileSync, realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { homedir } from "node:os";
import { pathToFileURL } from "node:url";

const profile = process.argv[2];
const emit = value => process.stdout.write(JSON.stringify(value) + "\n");
if (!["owlswatch", "hotel", "bailey-finance", "nomina"].includes(profile)) {
  emit({ status: "invalid_profile" }); process.exit(2);
}
const state = join(homedir(), `.openclaw-${profile}`);
process.env.OPENCLAW_STATE_DIR = state;
process.env.OPENCLAW_CONFIG_PATH = join(state, "openclaw.json");
// SDK diagnostic output is not part of this content-free interface.
for (const name of ["log", "info", "warn", "debug", "error"]) console[name] = () => {};
try {
  const binary = execFileSync("which", ["openclaw"], { encoding: "utf8", timeout: 5000 }).trim();
  const sdk = dirname(realpathSync(binary));
  const { resolveApiKeyForProvider } = await import(pathToFileURL(join(sdk, "dist/plugin-sdk/provider-auth-runtime.js")).href);
  const cfg = JSON.parse(readFileSync(process.env.OPENCLAW_CONFIG_PATH, "utf8"));
  let apiKey;
  for (const agent of cfg.agents?.list || []) {
    try {
      const auth = await resolveApiKeyForProvider({ provider: "deepseek", cfg,
        agentDir: agent.agentDir?.replace(/^~\//, homedir() + "/") || join(state, "agents", agent.id, "agent"),
        workspaceDir: agent.workspace?.replace(/^~\//, homedir() + "/") });
      if (auth.mode === "api-key" && typeof auth.apiKey === "string" && auth.apiKey) { apiKey = auth.apiKey; break; }
    } catch { /* A later configured specialist may own the existing credential. */ }
  }
  if (!apiKey) { emit({ status: "credential_unavailable" }); process.exit(0); }
  const response = await fetch("https://api.deepseek.com/user/balance", {
    headers: { Authorization: `Bearer ${apiKey}` }, signal: AbortSignal.timeout(10000), redirect: "error",
  });
  if (!response.ok) { emit({ status: "provider_error", httpStatus: response.status }); process.exit(0); }
  const raw = await response.text();
  if (raw.length > 65536) throw new Error("oversize");
  const data = JSON.parse(raw);
  const balances = (Array.isArray(data.balance_infos) ? data.balance_infos : [])
    .filter(value => ["USD", "CNY"].includes(value.currency) && typeof value.total_balance === "string"
      && /^\d+(?:\.\d+)?$/.test(value.total_balance) && Number.isFinite(Number(value.total_balance)))
    .map(value => ({ currency: value.currency, total: Number(value.total_balance) }));
  if (typeof data.is_available !== "boolean" || !balances.length) throw new Error("invalid_contract");
  emit({ status: "ok", available: data.is_available, balances });
} catch {
  emit({ status: "check_failed" });
}
