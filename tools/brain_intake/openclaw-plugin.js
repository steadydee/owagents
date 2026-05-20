import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { spawn } from "node:child_process";

const SERVER = "/Users/agent/.openclaw/workspace-dennis-brain/tools/brain_intake/server.py";
const BASE_ENV = {
  OPENCLAW_CONFIG_PATH: "/Users/agent/.openclaw-owlswatch/openclaw.json",
  BRAIN_API_BASE_URL: "http://127.0.0.1:3000"
};

const toolSchemas = {
  brain_health_check: {
    description: "Check whether the local Brain app is reachable.",
    parameters: { type: "object", properties: {}, additionalProperties: false }
  },
  brain_submit_intake: {
    description: "Submit a text update to Brain Intake and return the Brain receipt without sending Telegram.",
    parameters: {
      type: "object",
      properties: {
        raw_text: { type: "string" },
        sender_name: { type: "string" },
        sender_id: { type: ["string", "number"] },
        chat_title: { type: "string" },
        message_id: { type: ["string", "number"] },
        message_thread_id: { type: ["string", "number"] },
        domain_hint: { type: "string" },
        project_hint: { type: "string" }
      },
      required: ["raw_text"],
      additionalProperties: false
    }
  },
  brain_submit_telegram_update: {
    description: "Submit a Telegram text update to Brain Intake and send the Brain receipt back to the same chat.",
    parameters: {
      type: "object",
      properties: {
        raw_text: { type: "string" },
        chat_id: { type: ["string", "number"] },
        message_thread_id: { type: ["string", "number"] },
        reply_to_message_id: { type: ["string", "number"] },
        message_id: { type: ["string", "number"] },
        sender_name: { type: "string" },
        sender_id: { type: ["string", "number"] },
        chat_title: { type: "string" },
        domain_hint: { type: "string" },
        project_hint: { type: "string" }
      },
      required: ["raw_text", "chat_id"],
      additionalProperties: false
    }
  },
  brain_telegram_send_message: {
    description: "Send a direct Telegram Bot API message for Brain receipt or text-only notices.",
    parameters: {
      type: "object",
      properties: {
        chat_id: { type: ["string", "number"] },
        text: { type: "string" },
        reply_to_message_id: { type: ["string", "number"] },
        message_thread_id: { type: ["string", "number"] }
      },
      required: ["chat_id", "text"],
      additionalProperties: false
    }
  }
};

function jsonResult(value) {
  return {
    content: [{ type: "text", text: JSON.stringify(value) }],
    details: { structuredContent: value, status: value?.ok === false ? "error" : "ok" }
  };
}

function callPythonTool(name, args) {
  return new Promise((resolve) => {
    const child = spawn("python3", [SERVER, "call", name], {
      env: { ...process.env, ...BASE_ENV },
      stdio: ["pipe", "pipe", "pipe"]
    });
    let out = "";
    child.stdout.on("data", (chunk) => { out += chunk.toString(); });
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
  id: "brain-intake",
  name: "Brain Intake Tools",
  description: "Narrow tools for routing OpenClaw Telegram updates into Brain.",
  register(api) {
    for (const [name, spec] of Object.entries(toolSchemas)) {
      api.registerTool({
        name,
        label: name,
        description: spec.description,
        parameters: spec.parameters,
        execute: async (_toolCallId, rawParams) => callPythonTool(name, rawParams)
      }, { name });
    }
  }
});
