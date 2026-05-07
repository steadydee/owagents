import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { spawn } from "node:child_process";

const SERVER = "/Users/agent/.openclaw/workspace-owlswatch/tools/owlswatch_intake/server.py";
const BASE_ENV = {
  OWLSWATCH_WORKSPACE: "/Users/agent/.openclaw/workspace-owlswatch",
  OPENCLAW_CONFIG_PATH: "/Users/agent/.openclaw-owlswatch/openclaw.json"
};

const toolSchemas = {
  owlswatch_telegram_get_file: {
    description: "Get Telegram file metadata for a receipt photo.",
    parameters: { type: "object", properties: { file_id: { type: "string" } }, required: ["file_id"], additionalProperties: false }
  },
  owlswatch_telegram_download_file: {
    description: "Download a Telegram file or copy an OpenClaw inbound media file into the durable owlswatch spool.",
    parameters: { type: "object", properties: { file_id: { type: "string" }, openclaw_media_path: { type: "string" }, source_message_id: { type: ["string", "number"] }, index: { type: "integer", minimum: 1, maximum: 20 } }, required: ["source_message_id", "index"], additionalProperties: false }
  },
  owlswatch_telegram_send_message: {
    description: "Send a direct Telegram Bot API message. For forum topics, include message_thread_id.",
    parameters: { type: "object", properties: { chat_id: { type: ["string", "number"] }, text: { type: "string" }, reply_to_message_id: { type: ["string", "number"] }, message_thread_id: { type: ["string", "number"] } }, required: ["chat_id", "text"], additionalProperties: false }
  },
  owlswatch_telegram_send_chat_action: {
    description: "Show a short Telegram processing indicator such as typing dots. For forum topics, include message_thread_id.",
    parameters: { type: "object", properties: { chat_id: { type: ["string", "number"] }, action: { type: "string", enum: ["typing", "upload_photo"] }, message_thread_id: { type: ["string", "number"] } }, required: ["chat_id"], additionalProperties: false }
  },
  owlswatch_album_buffer_store: {
    description: "Store one album photo arrival in durable spool state.",
    parameters: { type: "object", properties: { media_group_id: { type: "string" }, chat_id: { type: ["string", "number"] }, file_id: { type: "string" }, caption_if_present: { type: ["string", "null"] }, source_message_id: { type: ["string", "number"] } }, required: ["media_group_id", "chat_id", "file_id"], additionalProperties: false }
  },
  owlswatch_album_buffer_check: {
    description: "Wait for album quiet period and atomically claim if complete.",
    parameters: { type: "object", properties: { media_group_id: { type: "string" }, chat_id: { type: ["string", "number"] }, claim_owner: { type: "string" }, quiet_seconds: { type: "number", minimum: 0, maximum: 30 } }, required: ["media_group_id", "chat_id"], additionalProperties: false }
  },
  owlswatch_operations_upload_attachment: {
    description: "Upload spooled receipt photos to Operations intake attachment endpoint.",
    parameters: { type: "object", properties: { local_paths: { type: "array", items: { type: "string" }, minItems: 1, maxItems: 10 } }, required: ["local_paths"], additionalProperties: false }
  },
  owlswatch_operations_create_expense_draft: {
    description: "Create an Operations expense draft with idempotency. Prefer arguments shaped exactly as { payload: { idempotencyKey, propertyId, source, sourceMessageId, submittedBy, expense, attachments, agent } }.",
    parameters: { type: "object", properties: { payload: { type: "object", additionalProperties: true } }, required: ["payload"], additionalProperties: true }
  },
  owlswatch_vision_extract_receipt: {
    description: "Extract receipt fields from uploaded blobs using configured vision provider.",
    parameters: { type: "object", properties: { blob_urls: { type: "array", items: { type: "string" }, minItems: 1, maxItems: 10 }, user_caption_if_present: { type: ["string", "null"] } }, required: ["blob_urls"], additionalProperties: false }
  },
  owlswatch_memory_log: {
    description: "Append one intake summary line to Cuenta memory.",
    parameters: { type: "object", properties: { content: { type: "string" } }, required: ["content"], additionalProperties: false }
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
  id: "owlswatch-intake",
  name: "Owl's Watch Intake Tools",
  description: "Narrow first-class tools for Cuenta receipt intake.",
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
