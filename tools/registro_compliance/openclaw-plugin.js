import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const SERVER = process.env.REGISTRO_COMPLIANCE_SERVER || join(HERE, "server.py");
const BASE_ENV = {
  REGISTRO_WORKSPACE: process.env.REGISTRO_WORKSPACE || "/Users/agent/.openclaw/workspace-owlswatch-registro",
  OPENCLAW_CONFIG_PATH: process.env.OPENCLAW_CONFIG_PATH || "/Users/agent/.openclaw-owlswatch/openclaw.json",
};

const toolSchemas = {
  registro_list_pending: {
    description: "List PMS registration rows needing extraction or due SIRE work.",
    parameters: { type: "object", properties: { limit: { type: ["integer", "null"] } }, additionalProperties: false },
  },
  registro_get: {
    description: "Get one PMS registration with reservation and submission context.",
    parameters: { type: "object", properties: { registrationId: { type: "string" } }, required: ["registrationId"], additionalProperties: false },
  },
  registro_fetch_media: {
    description: "Read the PMS media reference for a registration without exposing image bytes to the model.",
    parameters: { type: "object", properties: { registrationId: { type: "string" } }, required: ["registrationId"], additionalProperties: false },
  },
  registro_parse_mrz: {
    description: "Parse and checksum a two-line TD3 passport MRZ.",
    parameters: { type: "object", properties: { lines: { type: ["string", "array"] } }, required: ["lines"], additionalProperties: false },
  },
  registro_extract_document_vision: {
    description: "Call the configured local or tailnet vision extractor for a contained local path or private URL.",
    parameters: {
      type: "object",
      properties: { localPath: { type: ["string", "null"] }, imageUrl: { type: ["string", "null"] } },
      additionalProperties: false,
    },
  },
  registro_delete_media: {
    description: "Delete a fetched local media file from the Registro media spool.",
    parameters: { type: "object", properties: { localPath: { type: "string" } }, required: ["localPath"], additionalProperties: false },
  },
  registro_record_extraction: {
    description: "Record extracted document fields and validation errors in PMS.",
    parameters: { type: "object", properties: { registrationId: { type: "string" } }, required: ["registrationId"], additionalProperties: true },
  },
  registro_set_status: {
    description: "Move a registration through the PMS guarded status machine.",
    parameters: {
      type: "object",
      properties: { registrationId: { type: "string" }, status: { type: "string" } },
      required: ["registrationId", "status"],
      additionalProperties: false,
    },
  },
  registro_flag_exception: {
    description: "Flag a registration exception in PMS.",
    parameters: {
      type: "object",
      properties: { registrationId: { type: "string" }, reason: { type: "string" } },
      required: ["registrationId", "reason"],
      additionalProperties: false,
    },
  },
  registro_record_submission: {
    description: "Record a SIRE or TRA submission attempt in PMS.",
    parameters: { type: "object", properties: { registrationId: { type: "string" } }, required: ["registrationId"], additionalProperties: true },
  },
  registro_request_guest_fix: {
    description: "Ask Luna to send one guest correction request inside the WhatsApp service window.",
    parameters: {
      type: "object",
      properties: {
        registrationId: { type: "string" },
        reason: { type: ["string", "null"] },
        message: { type: ["string", "null"] },
      },
      required: ["registrationId"],
      additionalProperties: false,
    },
  },
  registro_telegram_notify: {
    description: "Send a staff-facing Telegram notification about a registration exception or sweep result.",
    parameters: {
      type: "object",
      properties: {
        text: { type: "string" },
        chat_id: { type: ["string", "number", "null"] },
        message_thread_id: { type: ["string", "number", "null"] },
      },
      required: ["text"],
      additionalProperties: false,
    },
  },
};

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
  id: "registro-compliance",
  name: "Registro Compliance Tools",
  description: "PMS/Luna compliance tools for the Registro agent.",
  register(api) {
    for (const [name, spec] of Object.entries(toolSchemas)) {
      api.registerTool(
        {
          name,
          label: name,
          description: spec.description,
          parameters: spec.parameters,
          execute: async (_toolCallId, rawParams) => callPythonTool(name, rawParams),
        },
        { name },
      );
    }
  },
});
