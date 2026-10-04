import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { callPython, trustedEnvironment, toolTimeout } from "./tool-bridge.mjs";

async function fixture(body, callback) {
  const dir = mkdtempSync(join(tmpdir(), "nomina-bridge-"));
  const server = join(dir, "server.cjs");
  writeFileSync(server, body);
  try { return await callback({ server, env: { ...process.env }, python: process.execPath, name: "nomina_status", timeoutMs: 2000 }); }
  finally { rmSync(dir, { recursive: true, force: true }); }
}

test("bridge overwrites inherited approval and passes host context separately from arguments", async () => {
  const old = { OWLSWATCH_PAYROLL_TRUSTED_CONTEXT: '{"authorized":true}' };
  assert.deepEqual(JSON.parse(trustedEnvironment(old, null).OWLSWATCH_PAYROLL_TRUSTED_CONTEXT), {});
  assert.equal(old.OWLSWATCH_PAYROLL_TRUSTED_CONTEXT, '{"authorized":true}');
  await fixture(`let text=''; process.stdin.on('data', c=>text+=c); process.stdin.on('end', ()=>console.log(JSON.stringify({ok:true,result:{args:JSON.parse(text),trusted:JSON.parse(process.env.OWLSWATCH_PAYROLL_TRUSTED_CONTEXT),argv:process.argv.slice(2)}})));`, async options => {
    const trusted = { senderId: "101", source: "tool_context" };
    const result = await callPython({ ...options, args: { confirmed: true }, env: { ...options.env, ...old }, trusted });
    assert.deepEqual(result.result.trusted, trusted);
    assert.deepEqual(result.result.args, { confirmed: true });
    assert.deepEqual(result.result.argv, ["call", "nomina_status"]);
  });
});

test("native token travels as fixed approve argument only", async () => {
  await fixture(`process.stdin.resume(); process.stdin.on('end',()=>console.log(JSON.stringify({ok:true,result:process.argv.slice(2)})));`, async options => {
    const result = await callPython({ ...options, command: "approve", name: "ABCDEF0123456789" });
    assert.deepEqual(result.result, ["approve", "ABCDEF0123456789"]);
    const reviewed = await callPython({ ...options, command: "review", name: "ABCDEF0123456789" });
    assert.deepEqual(reviewed.result, ["review", "ABCDEF0123456789"]);
    assert.equal((await callPython({ ...options, command: "approve", name: "ABCDEF0123456789;echo" })).error.code, "invalid_bridge_request");
  });
});

test("only archive upload receives the longer bounded timeout", () => {
  assert.equal(toolTimeout("nomina_archive_status"), 180000);
  assert.equal(toolTimeout("nomina_prepare_paid"), 45000);
  assert.equal(toolTimeout("ABCDEF0123456789"), 45000);
});

test("nonzero exit and malformed/oversize outputs are uncertain without leaking stderr", async () => {
  for (const [body, code, extra] of [
    ["process.stderr.write('private diagnostic'); process.exit(2)", "tool_process_failed", {}],
    ["console.log('not JSON')", "invalid_tool_response", {}],
    ["console.log('{}')", "invalid_tool_response", {}],
    ["console.log('x'.repeat(2048))", "tool_response_too_large", { maxOutputBytes: 512 }],
  ]) await fixture(body, async options => {
    const result = await callPython({ ...options, ...extra });
    assert.equal(result.error.code, code);
    assert.equal(result.error.uncertain, true);
    assert.equal(result.error.retryable, false);
    assert.ok(!JSON.stringify(result).includes("private diagnostic"));
  });
});

test("timeouts stop child and preserve uncertain outcome", async () => {
  await fixture("process.stdin.resume(); setTimeout(()=>console.log('{\"ok\":true}'),5000);", async options => {
    const result = await callPython({ ...options, timeoutMs: 100 });
    assert.equal(result.error.code, "tool_timeout");
    assert.equal(result.error.uncertain, true);
  });
});

test("failure to spawn is known not executed; invalid input never spawns", async () => {
  const result = await callPython({ server: "missing.py", env: {}, python: "/nonexistent/nomina-interpreter", name: "nomina_status" });
  assert.equal(result.error.code, "tool_start_failed");
  assert.equal(result.error.uncertain, false);
  assert.equal((await callPython({ name: "exec" })).error.code, "invalid_bridge_request");
  assert.equal((await callPython({ name: "nomina_status", args: { x: "x".repeat(70000) } })).error.code, "tool_input_too_large");
});
