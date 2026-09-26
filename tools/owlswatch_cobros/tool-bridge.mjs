import { spawn } from "node:child_process";

export function callPythonTool(server, env, name, args, timeoutMs = name.includes("create_") ? 120000 : 45000) {
  return new Promise(resolve => {
    const child = spawn("python3", [server, "call", name], { env, stdio: ["pipe", "pipe", "pipe"] });
    let out = "", size = 0, settled = false;
    const finish = value => { if (!settled) { settled = true; clearTimeout(timer); resolve(value); } };
    const fail = code => {
      child.kill("SIGKILL");
      finish({ ok: false, error: { code, retryable: false, message: name.includes("create_")
        ? "The write outcome is unconfirmed. Report this and check existing artifacts before retrying."
        : "The lookup failed. Stop and ask for missing information or report that the source could not be read." } });
    };
    const timer = setTimeout(() => fail("tool_timeout"), timeoutMs);
    child.stderr.resume();
    child.on("error", () => fail("tool_start_failed"));
    child.stdin.on("error", () => fail("tool_input_failed"));
    child.stdout.on("data", chunk => {
      size += chunk.length;
      if (size > 65536) return fail("tool_response_too_large");
      out += chunk.toString();
    });
    child.on("close", code => {
      if (settled) return;
      if (code !== 0) return fail("tool_process_failed");
      try { finish(JSON.parse(out)); } catch { fail("tool_bridge_error"); }
    });
    child.stdin.end(JSON.stringify(args ?? {}));
  });
}
