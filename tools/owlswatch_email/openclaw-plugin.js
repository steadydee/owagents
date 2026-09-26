import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
import path from "node:path";

const SERVER = fileURLToPath(new URL("./server.py", import.meta.url));
const PYTHON = process.env.OWLSWATCH_EMAIL_PYTHON || "python3";
const BASE_ENV = {
  ...process.env,
  OWLSWATCH_EMAIL_WORKSPACE: process.env.OWLSWATCH_EMAIL_WORKSPACE || path.join(homedir(), ".openclaw/workspace-owlswatch-correo"),
  OPENCLAW_CONFIG_PATH: process.env.OPENCLAW_CONFIG_PATH || path.join(homedir(), ".openclaw-owlswatch/openclaw.json")
};

function jsonResult(value) {
  return { content: [{ type: "text", text: JSON.stringify(value) }], details: { structuredContent: value, status: value?.ok === false ? "error" : "ok" } };
}
function bridgeError() {
  return jsonResult({ ok: false, error: { code: "tool_bridge_error", message: "The Correo tool process did not return a valid result.", retryable: false } });
}
function callPythonTool(name, args) {
  return new Promise((resolve) => {
    const child = spawn(PYTHON, [SERVER, "call", name], { env: BASE_ENV, stdio: ["pipe", "pipe", "pipe"] });
    let out = "";
    const timeout = setTimeout(() => child.kill("SIGTERM"), 120000);
    child.stdout.on("data", (chunk) => { out += chunk.toString(); if (out.length > 2000000) child.kill("SIGTERM"); });
    child.on("error", () => { clearTimeout(timeout); resolve(bridgeError()); });
    child.on("close", (code) => {
      clearTimeout(timeout);
      try { resolve(code === 0 ? jsonResult(JSON.parse(out)) : bridgeError()); }
      catch { resolve(bridgeError()); }
    });
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify(args ?? {}));
  });
}

export default definePluginEntry({
  id: "owlswatch-email",
  name: "Owl's Watch Email Tools",
  description: "Narrow Correo drafting tools; Gmail is the human review surface.",
  register(api) {
    const catalog = spawnSync(PYTHON, [SERVER], { env: BASE_ENV, input: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list" }) + "\n", encoding: "utf8", timeout: 10000, maxBuffer: 1000000 });
    if (catalog.status !== 0) throw new Error("Correo tool catalog could not be loaded.");
    const tools = JSON.parse(catalog.stdout).result.tools;
    for (const tool of tools) {
      api.registerTool({ name: tool.name, label: tool.name, description: tool.description, parameters: tool.inputSchema,
        execute: async (_toolCallId, rawParams) => callPythonTool(tool.name, rawParams)
      }, { name: tool.name });
    }
  }
});
