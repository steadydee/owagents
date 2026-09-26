import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { appendFileSync, chmodSync, existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createGuard } from "./guard.mjs";

export default definePluginEntry({
  id: "owlswatch-runtime-guard",
  name: "Owl's Watch Runtime Guard",
  register(api) {
    const stateDir = process.env.OPENCLAW_STATE_DIR || dirname(dirname(dirname(fileURLToPath(import.meta.url))));
    const dir = join(stateDir, "health");
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    const circuitPath = join(dir, "provider-circuit.json");
    const record = value => {
      const path = join(dir, "runs.jsonl");
      if (existsSync(path) && statSync(path).size > 5 * 1024 * 1024) renameSync(path, path + ".previous");
      appendFileSync(path, JSON.stringify({ at: new Date().toISOString(), ...value }) + "\n", { mode: 0o600 });
      chmodSync(path, 0o600);
    };
    const guard = createGuard({
      record,
      readCircuit: () => { try { return JSON.parse(readFileSync(circuitPath, "utf8")); } catch { return {}; } },
      writeCircuit: value => {
        const temp = circuitPath + `.${process.pid}.tmp`;
        writeFileSync(temp, JSON.stringify(value), { mode: 0o600 });
        renameSync(temp, circuitPath);
      },
    });
    api.on("before_agent_run", (event, ctx) => guard.beforeRun(event, ctx));
    api.on("before_tool_call", (event, ctx) => guard.beforeTool(event, ctx));
    api.on("model_call_ended", (event, ctx) => guard.modelCall(event, ctx));
    api.on("llm_output", (event, ctx) => guard.output(event, ctx));
    api.on("agent_end", (event, ctx) => guard.end(event, ctx));
  },
});
