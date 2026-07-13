import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const SERVER = process.env.FINCA_TASKS_SERVER || join(HERE, "server.py");
const BASE_ENV = {
  ...process.env,
  FINCA_WORKSPACE: process.env.FINCA_WORKSPACE || `${process.env.HOME}/.openclaw/workspace-finca-ops`,
  OPENCLAW_CONFIG_PATH: process.env.OPENCLAW_CONFIG_PATH || `${process.env.HOME}/.openclaw-finca/openclaw.json`,
  OPENCLAW_STATE_DIR: process.env.OPENCLAW_STATE_DIR || `${process.env.HOME}/.openclaw-finca`,
};

function loadCatalog() {
  const result = spawnSync("python3", [SERVER, "list"], { env: BASE_ENV, encoding: "utf8" });
  if (result.status !== 0) throw new Error("Finca tool catalog could not be loaded.");
  const catalog = JSON.parse(result.stdout || "[]");
  if (!Array.isArray(catalog)) throw new Error("Finca tool catalog is invalid.");
  return catalog;
}

function jsonResult(value) {
  return {
    content: [{ type: "text", text: JSON.stringify(value) }],
    details: { structuredContent: value, status: value?.ok === false ? "error" : "ok" },
  };
}

function callPythonTool(name, args) {
  return new Promise((resolve) => {
    const child = spawn("python3", [SERVER, "call", name], {
      env: BASE_ENV,
      stdio: ["pipe", "pipe", "ignore"],
    });
    let output = "";
    child.stdout.on("data", (chunk) => { output += chunk.toString(); });
    child.on("close", () => {
      try {
        resolve(jsonResult(JSON.parse(output || "{}")));
      } catch {
        resolve(jsonResult({
          ok: false,
          error: { code: "tool_bridge_error", message: "Finca tool bridge returned invalid JSON.", retryable: false },
        }));
      }
    });
    child.stdin.end(JSON.stringify(args ?? {}));
  });
}

export default definePluginEntry({
  id: "finca-tasks",
  name: "OW Finca Task Tools",
  description: "Narrow Operations task and Telegram tools for the OW Finca agent.",
  register(api) {
    for (const tool of loadCatalog()) {
      api.registerTool({
        name: tool.name,
        label: tool.name,
        description: tool.description,
        parameters: tool.inputSchema,
        execute: async (_toolCallId, rawParams) => callPythonTool(tool.name, rawParams),
      }, { name: tool.name });
    }
  },
});
