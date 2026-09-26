import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { spawn, execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { createReadBudget } from "./read-budget.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const SERVER = resolve(TOOL_DIR, "server.py");
const WORKSPACE = process.env.OWLSWATCH_COBROS_WORKSPACE || resolve(TOOL_DIR, "../..");
const BASE_ENV = { OWLSWATCH_COBROS_WORKSPACE: WORKSPACE, PYTHONDONTWRITEBYTECODE: "1" };

function jsonResult(value) {
  return { content: [{ type: "text", text: JSON.stringify(value) }],
    details: { structuredContent: value, status: value?.ok === false ? "error" : "ok" } };
}

function callPythonTool(name, args) {
  return new Promise((resolveResult) => {
    const child = spawn("python3", [SERVER, "call", name], {
      env: { ...process.env, ...BASE_ENV }, stdio: ["pipe", "pipe", "ignore"]
    });
    let out = "", finished = false;
    const finish = (value) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      resolveResult(jsonResult(value));
    };
    const bridgeError = () => finish({ ok: false, error: { code: "tool_bridge_error",
      message: "Tool process did not return a verified result. For writes, retry the same preparedId only to reconcile; never prepare a replacement.", retryable: false } });
    const timer = setTimeout(() => { child.kill("SIGTERM"); bridgeError(); }, 120_000);
    child.on("error", bridgeError);
    child.stdout.on("data", (chunk) => {
      out += chunk.toString();
      if (out.length > 1_000_000) { child.kill("SIGTERM"); bridgeError(); }
    });
    child.on("close", () => {
      try { finish(JSON.parse(out)); } catch { bridgeError(); }
    });
    child.stdin.on("error", bridgeError);
    child.stdin.end(JSON.stringify(args ?? {}));
  });
}

export default definePluginEntry({
  id: "owlswatch-cobros", name: "Owl's Watch Cobros Tools",
  description: "Narrow tools for Cobros cuenta de cobro drafting.",
  register(api) {
    const catalog = JSON.parse(execFileSync("python3", [SERVER, "catalog"], {
      env: { ...process.env, ...BASE_ENV }, encoding: "utf8", timeout: 10_000, maxBuffer: 200_000
    }));
    api.on("before_tool_call", createReadBudget());
    for (const [name, spec] of Object.entries(catalog)) {
      api.registerTool({ name, label: name, description: spec.description,
        parameters: spec.parameters, execute: async (_toolCallId, rawParams) => callPythonTool(name, rawParams)
      }, { name });
    }
  }
});
