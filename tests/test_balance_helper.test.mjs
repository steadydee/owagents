import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { checkBalance, configuredAgents } from "../scripts/check-deepseek-balance.mjs";

test("both schemas resolve each specialist's own identity and inherited workspace", async () => {
  for (const section of [
    { list: [{ id: "correo" }, { id: "cotiza", agentDir: "~/cotiza-agent", workspace: "~/quotes" }] },
    { entries: { correo: {}, cotiza: { agentDir: "~/cotiza-agent", workspace: "~/quotes" } } },
  ]) {
    const cfg = { agents: { defaults: { workspace: "~/default-workspace" }, ...section } };
    const calls = [];
    let fetchCount = 0;
    const result = await checkBalance(cfg, { state: "/fixture/state", home: "/fixture/home",
      resolveAuth: async request => {
        calls.push(request);
        if (calls.length === 1) throw new Error("fixture unavailable");
        return { mode: "api-key", apiKey: "fixture-private-value" };
      },
      fetchBalance: async (url, options) => {
        fetchCount++;
        assert.equal(url, "https://api.deepseek.com/user/balance");
        assert.equal(options.redirect, "error");
        assert.equal(options.headers.Authorization, "Bearer fixture-private-value");
        return { ok: true, text: async () => JSON.stringify({ is_available: true,
          balance_infos: [{ currency: "USD", total_balance: "8.50" }], ignoredSecret: "fixture-private-value" }) };
      },
    });
    assert.equal(calls[0].agentDir, "/fixture/state/agents/correo/agent");
    assert.equal(calls[0].workspaceDir, "/fixture/home/default-workspace");
    assert.equal(calls[1].agentDir, "/fixture/home/cotiza-agent");
    assert.equal(calls[1].workspaceDir, "/fixture/home/quotes");
    assert.equal(fetchCount, 1);
    assert.deepEqual(result, { status: "ok", available: true, balances: [{ currency: "USD", total: 8.5 }] });
    assert.ok(!JSON.stringify(result).includes("fixture-private-value"));
  }
});

test("conflicting or unsafe identities fail before any credential lookup", async () => {
  for (const agents of [
    { list: [{ id: "correo" }], entries: { correo: {} } },
    { entries: { correo: { id: "cotiza" } } },
    { entries: { "../other-profile": {} } },
    { list: [{ id: "correo" }, { id: "correo" }] },
  ]) {
    assert.throws(() => configuredAgents({ agents }));
    await assert.rejects(checkBalance({ agents }, { state: "/fixture", home: "/fixture",
      resolveAuth: () => assert.fail("invalid identity must not inspect auth"),
      fetchBalance: () => assert.fail("invalid identity must not request balance") }));
  }
});

test("missing API-key credential cannot query the provider", async () => {
  const result = await checkBalance({ agents: { entries: { nomina: {} } } }, {
    state: "/fixture", home: "/fixture", resolveAuth: async () => ({ mode: "oauth", apiKey: "unused" }),
    fetchBalance: () => assert.fail("OAuth is not an API billing credential"),
  });
  assert.deepEqual(result, { status: "credential_unavailable" });
});

test("CLI without explicit SDK does not inspect PATH or a profile", () => {
  const home = mkdtempSync(join(tmpdir(), "balance-unconfigured-"));
  try {
    const script = new URL("../scripts/check-deepseek-balance.mjs", import.meta.url);
    const output = execFileSync(process.execPath, [script.pathname, "hotel"],
      { env: { HOME: home, PATH: "/no-binaries" }, encoding: "utf8" });
    assert.deepEqual(JSON.parse(output), { status: "runtime_unconfigured" });
  } finally { rmSync(home, { recursive: true, force: true }); }
});
