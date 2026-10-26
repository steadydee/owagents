import { spawn } from "node:child_process";

const isWrite = name => /(?:create|revise|update|memory_log)/.test(name);

export function callPythonTool({ server, env, name, args, signal,
  timeoutMs = isWrite(name) ? 120000 : 45000,
  killGraceMs = 250, maxOutputBytes = 262144, spawnImpl = spawn }) {
  return new Promise(resolve => {
    let child, deadline, killTimer, reapTimer, settled = false, failure;
    let output = [], bytes = 0;
    const error = code => ({ ok: false, error: { code, retryable: false,
      message: isWrite(name)
        ? "The write outcome is unconfirmed. Reconcile the same quote and source idempotency key before retrying; do not create a replacement or claim completion."
        : "The quote lookup did not complete. Report the failure and stop this attempt." } });
    const finish = value => {
      if (settled) return;
      settled = true;
      clearTimeout(deadline); clearTimeout(killTimer); clearTimeout(reapTimer);
      signal?.removeEventListener("abort", abort);
      resolve(value);
    };
    const kill = sig => {
      if (!child?.pid) return;
      try {
        if (process.platform === "win32") child.kill(sig);
        else process.kill(-child.pid, sig);
      } catch { /* The task-owned process group may already have exited. */ }
    };
    const terminate = code => {
      if (settled || failure) return;
      failure = code;
      kill("SIGTERM");
      killTimer = setTimeout(() => {
        kill("SIGKILL");
        // Bound resolution even if a broken child leaves inherited pipes open.
        reapTimer = setTimeout(() => finish(error(failure)), 250);
      }, killGraceMs);
    };
    const abort = () => terminate("tool_cancelled");
    if (signal?.aborted) { finish(error("tool_cancelled")); return; }
    try {
      child = spawnImpl("python3", [server, "call", name], {
        env, stdio: ["pipe", "pipe", "pipe"], detached: process.platform !== "win32"
      });
    } catch { finish(error("tool_start_failed")); return; }
    deadline = setTimeout(() => terminate("tool_timeout"), timeoutMs);
    signal?.addEventListener("abort", abort, { once: true });
    child.stderr.resume(); // Drain without retaining or exposing provider diagnostics.
    child.on("error", () => {
      if (!child.pid) finish(error("tool_start_failed"));
      else terminate("tool_process_failed");
    });
    child.stdin.on("error", () => terminate("tool_input_failed"));
    child.stdout.on("error", () => terminate("tool_output_failed"));
    child.stderr.on("error", () => terminate("tool_output_failed"));
    child.stdout.on("data", chunk => {
      if (failure || settled) return;
      bytes += chunk.length;
      if (bytes > maxOutputBytes) { terminate("tool_response_too_large"); return; }
      output.push(chunk);
    });
    child.on("close", code => {
      // Kill any task-owned descendants before settling an interrupted call.
      if (failure) { kill("SIGKILL"); finish(error(failure)); return; }
      if (code !== 0) { finish(error("tool_process_failed")); return; }
      try {
        const value = JSON.parse(Buffer.concat(output).toString("utf8"));
        if (!value || Array.isArray(value) || typeof value !== "object") throw new Error();
        finish(value);
      } catch { finish(error("tool_bridge_error")); }
    });
    try { child.stdin.end(JSON.stringify(args ?? {})); }
    catch { terminate("tool_input_failed"); }
  });
}
