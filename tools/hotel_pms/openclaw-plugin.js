import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const SERVER = process.env.HOTEL_PMS_SERVER || join(HERE, "server.py");
const BASE_ENV = {
  HOTEL_PMS_WORKSPACE: process.env.HOTEL_PMS_WORKSPACE || "/Users/agent/.openclaw/workspace-hotel-ops",
  OPENCLAW_CONFIG_PATH: process.env.OPENCLAW_CONFIG_PATH || "/Users/agent/.openclaw-hotel/openclaw.json",
};

async function loadToolSchemas() {
  return new Promise((resolve) => {
    const child = spawn("python3", [SERVER, "list"], {
      env: { ...process.env, ...BASE_ENV },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let out = "";
    child.stdout.on("data", (chunk) => {
      out += chunk.toString();
    });
    child.on("close", () => {
      try {
        const parsed = JSON.parse(out || "{}");
        resolve(Array.isArray(parsed.tools) ? parsed.tools : []);
      } catch {
        resolve([]);
      }
    });
  });
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
      env: { ...process.env, ...BASE_ENV },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let out = "";
    child.stdout.on("data", (chunk) => {
      out += chunk.toString();
    });
    child.on("close", () => {
      try {
        resolve(jsonResult(JSON.parse(out || "{}")));
      } catch {
        resolve(jsonResult({ ok: false, error: { code: "tool_bridge_error", message: "Tool bridge returned invalid JSON.", retryable: false } }));
      }
    });
    child.stdin.end(JSON.stringify(args ?? {}));
  });
}

export default definePluginEntry({
  id: "hotel-pms",
  name: "Hotel PMS Tools",
  description: "Read-only PMS tools and staff notifications for the Hotel operations agent.",
  async register(api) {
    const schemas = await loadToolSchemas();
    for (const spec of schemas) {
      api.registerTool(
        {
          name: spec.name,
          label: spec.name,
          description: spec.description,
          parameters: spec.parameters,
          execute: async (_toolCallId, rawParams) => callPythonTool(spec.name, rawParams),
        },
        { name: spec.name },
      );
    }
  },
});
