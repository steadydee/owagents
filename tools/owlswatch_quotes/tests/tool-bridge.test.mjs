import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { callPythonTool } from "../tool-bridge.mjs";

const server = fileURLToPath(new URL("./bridge-fixture.py", import.meta.url));
const call = options => callPythonTool({ server, env: process.env, name: "owlswatch_quote_prepare", timeoutMs: 3000, ...options });
test("accepts valid output and drains large stderr without exposing it", async () => {
  assert.deepEqual(await call({ args: { mode: "stderr" } }), { ok: true, fixture: "complete" });
});
for (const [mode, code] of [["invalid", "tool_bridge_error"], ["large", "tool_response_too_large"], ["nonzero", "tool_process_failed"]]) {
  test(`returns sanitized ${code}`, async () => {
    const result = await call({ args: { mode } });
    assert.equal(result.error.code, code);
    assert.equal(result.error.retryable, false);
    assert.equal(JSON.stringify(result).includes("private diagnostic"), false);
  });
}
test("handles executable failure without rejection or hanging", async () => {
  const result = await call({ env: { PATH: "/nonexistent-synthetic-bin" }, args: {} });
  assert.equal(result.error.code, "tool_start_failed");
});
test("pre-aborted calls do not spawn", async () => {
  const result = await call({ signal: AbortSignal.abort(), spawnImpl: () => { throw new Error("must not spawn"); } });
  assert.equal(result.error.code, "tool_cancelled");
});
for (const cancel of [false, true]) {
  test(`${cancel ? "cancellation" : "deadline"} terminates the tool and its child, leaving writes uncertain`, async () => {
    const dir = await mkdtemp(join(tmpdir(), "cotiza-bridge-"));
    const pidsPath = join(dir, "pids.json");
    let pids = [];
    try {
      const controller = new AbortController();
      const started = Date.now();
      const pending = call({ name: "owlswatch_quote_create_draft", args: { mode: "hang", pids: pidsPath },
        timeoutMs: cancel ? 3000 : 700, killGraceMs: 50, signal: controller.signal });
      for (let i = 0; i < 100; i++) {
        try { pids = JSON.parse(await readFile(pidsPath, "utf8")); break; } catch { await delay(10); }
      }
      assert.equal(pids.length, 2);
      if (cancel) controller.abort();
      const result = await pending;
      assert.equal(result.error.code, cancel ? "tool_cancelled" : "tool_timeout");
      assert.match(result.error.message, /outcome is unconfirmed.*same quote/);
      assert.equal(result.error.retryable, false);
      assert.ok(Date.now() - started < 2500);
      for (const pid of pids) {
        let alive = true;
        for (let i = 0; i < 100 && alive; i++) {
          try { process.kill(pid, 0); await delay(10); } catch { alive = false; }
        }
        assert.equal(alive, false, `fixture process ${pid} must be stopped`);
      }
    } finally {
      for (const pid of pids) { try { process.kill(pid, "SIGKILL"); } catch {} }
      await rm(dir, { recursive: true, force: true });
    }
  });
}
