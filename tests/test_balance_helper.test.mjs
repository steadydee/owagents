import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, realpathSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { checkBalance, configuredAgents, reserveBalanceBudget } from "../scripts/check-deepseek-balance.mjs";

test("both schemas resolve each specialist's own identity and inherited workspace", async () => {
  for (const section of [
    { list: [{ id: "correo" }, { id: "cotiza", agentDir: "~/cotiza-agent", workspace: "~/quotes" }] },
    { entries: { correo: {}, cotiza: { agentDir: "~/cotiza-agent", workspace: "~/quotes" } } },
  ]) {
    const cfg = { agents: { defaults: { workspace: "~/default-workspace" }, ...section } };
    const calls = [];
    let fetchCount = 0;
    const result = await checkBalance(cfg, { reserveBudget: async () => ({ status: "reserved" }), state: "/fixture/state", home: "/fixture/home",
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
    state: "/fixture", home: "/fixture", reserveBudget: () => assert.fail("no credential must spend no permit"), resolveAuth: async () => ({ mode: "oauth", apiKey: "unused" }),
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

const python = execFileSync("python3", ["-c", "import sys; print(sys.executable)"], { encoding: "utf8" }).trim();
const budgetScript = new URL("../scripts/monitor-http-budget.py", import.meta.url).pathname;
const cfg = { agents: { entries: { hotel: {} } } };
const auth = async () => ({ mode: "api-key", apiKey: "fixture-private-value" });
function fixture() {
  const dir = realpathSync(mkdtempSync(join(tmpdir(), "balance-budget-")));
  const budgetState = join(dir, "budget.sqlite3");
  const run = command => JSON.parse(execFileSync(python, [budgetScript, command, "--state", budgetState], { encoding: "utf8" }));
  return { dir, budgetState, run, clean: () => rmSync(dir, { recursive: true, force: true }) };
}
function options(f, profile = "hotel") {
  return { state: "/fixture", home: "/fixture", resolveAuth: auth,
    reserveBudget: () => reserveBalanceBudget({ budgetState: f.budgetState, budgetPython: python, profile }) };
}

test("actual Python ledger reserves before HTTP across three profiles; fourth attempt is blocked", async () => {
  const f = fixture();
  try {
    assert.equal(f.run("init").status, "initialized");
    let requests = 0;
    for (const profile of ["owlswatch", "hotel", "bailey-finance"]) {
      const result = await checkBalance(cfg, { ...options(f, profile), fetchBalance: async (_url, request) => {
        assert.equal(f.run("status").used.balance_24h, requests + 1);
        requests++;
        assert.equal(request.redirect, "error");
        assert.ok(request.signal instanceof AbortSignal);
        return { ok: false, status: 503 };
      } });
      assert.deepEqual(result, { status: "provider_error", httpStatus: 503 });
    }
    const denied = await checkBalance(cfg, { ...options(f), fetchBalance: () => assert.fail("exhausted budget must block HTTP") });
    assert.equal(denied.status, "budget_exhausted");
    assert.equal(requests, 3);
  } finally { f.clean(); }
});

test("timeout or redirect rejection consumes one permit without automatic retry", async () => {
  const f = fixture();
  try {
    f.run("init");
    let calls = 0;
    await assert.rejects(checkBalance(cfg, { ...options(f), fetchBalance: async () => {
      calls++;
      throw new Error("synthetic ambiguous transport failure");
    } }));
    assert.equal(calls, 1);
    assert.equal(f.run("status").used.balance_24h, 1);
    assert.equal(f.run("init").used.balance_24h, 1);
  } finally { f.clean(); }
});

test("missing or corrupted ledger and unavailable Python fail before HTTP", async () => {
  const f = fixture();
  try {
    const fetchBalance = () => assert.fail("unverified budget must block HTTP");
    assert.equal((await checkBalance(cfg, { ...options(f), fetchBalance })).status, "budget_unavailable");
    writeFileSync(f.budgetState, "corrupted fixture", { mode: 0o600 });
    assert.equal((await checkBalance(cfg, { ...options(f), fetchBalance })).status, "budget_unavailable");
    assert.equal((await reserveBalanceBudget({ budgetState: f.budgetState, budgetPython: join(f.dir, "missing-python"), profile: "hotel" })).status, "budget_unavailable");
    assert.equal((await checkBalance(cfg, { state: "/fixture", home: "/fixture", resolveAuth: auth, fetchBalance })).status, "budget_unconfigured");
  } finally { f.clean(); }
});

test("CLI refuses missing shared budget configuration before SDK/profile access", () => {
  const home = realpathSync(mkdtempSync(join(tmpdir(), "balance-no-budget-")));
  try {
    const script = new URL("../scripts/check-deepseek-balance.mjs", import.meta.url).pathname;
    const output = execFileSync(process.execPath, [script, "hotel", "--sdk-root", join(home, "no-sdk")],
      { env: { HOME: home, PATH: "/no-binaries" }, encoding: "utf8" });
    assert.deepEqual(JSON.parse(output), { status: "budget_unconfigured" });
  } finally { rmSync(home, { recursive: true, force: true }); }
});


test("paused Nomina cannot reserve or access profile/SDK via CLI", async () => {
  const f = fixture();
  try {
    f.run("init");
    assert.equal((await reserveBalanceBudget({ budgetState: f.budgetState, budgetPython: python, profile: "nomina" })).status, "budget_unconfigured");
    const script = new URL("../scripts/check-deepseek-balance.mjs", import.meta.url).pathname;
    assert.throws(() => execFileSync(process.execPath, [script, "nomina", "--sdk-root", join(f.dir, "no-sdk"),
      "--budget-state", f.budgetState, "--budget-python", python], {
      env: { HOME: f.dir, PATH: "/no-binaries" }, encoding: "utf8",
    }), error => error.status === 2 && JSON.parse(error.stdout).status === "invalid_profile");
    assert.equal(f.run("status").used.balance_24h, 0);
  } finally { f.clean(); }
});
