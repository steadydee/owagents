import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { createReadBudget } from "./read-budget.mjs";
import { createCobrosGuards } from "./run-guards.mjs";
import { callPythonTool as boundedPythonTool } from "./tool-bridge.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const SERVER = resolve(TOOL_DIR, "server.py");
const WORKSPACE = process.env.OWLSWATCH_COBROS_WORKSPACE || resolve(TOOL_DIR, "../..");
const BASE_ENV = { OWLSWATCH_COBROS_WORKSPACE: WORKSPACE, PYTHONDONTWRITEBYTECODE: "1" };

function jsonResult(value) {
  return { content: [{ type: "text", text: JSON.stringify(value) }],
    details: { structuredContent: value, status: value?.ok === false ? "error" : "ok" } };
}

function callPythonTool(name, args) {
  return boundedPythonTool(SERVER, { ...process.env, ...BASE_ENV }, name, args).then(jsonResult);
}

export default definePluginEntry({
  id: "owlswatch-cobros", name: "Owl's Watch Cobros Tools",
  description: "Narrow tools for Cobros cuenta de cobro drafting.",
  register(api) {
    const guards = createCobrosGuards(WORKSPACE);
    api.on("before_prompt_build", guards.prompt);
    api.on("before_tool_call", guards.beforeTool);
    api.on("agent_end", guards.end);
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
