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

const toolSchemas = {
  hotel_pms_get_tomorrow_arrivals: {
    description: "Return enriched PMS arrivals for tomorrow or a supplied YYYY-MM-DD date.",
    parameters: {
      type: "object",
      properties: { date: { type: ["string", "null"] } },
      additionalProperties: false,
    },
  },
  hotel_pms_list_arrivals: {
    description: "List PMS arrivals for a supplied date or today.",
    parameters: {
      type: "object",
      properties: { date: { type: ["string", "null"] } },
      additionalProperties: false,
    },
  },
  hotel_pms_find_reservation: {
    description: "Search PMS reservations by guest, email, reference, or status.",
    parameters: {
      type: "object",
      properties: {
        guestName: { type: ["string", "null"] },
        email: { type: ["string", "null"] },
        sourceReference: { type: ["string", "null"] },
        status: { type: ["string", "null"] },
      },
      additionalProperties: false,
    },
  },
  hotel_pms_get_reservation_context: {
    description: "Get read-only PMS reservation context, finance summary, checklist, and invoice metadata.",
    parameters: {
      type: "object",
      properties: { reservationId: { type: "string" } },
      required: ["reservationId"],
      additionalProperties: false,
    },
  },
  hotel_pms_get_dashboard_snapshot: {
    description: "Get the PMS dashboard snapshot.",
    parameters: { type: "object", properties: {}, additionalProperties: false },
  },
  hotel_pms_get_lifecycle_snapshot: {
    description: "Get the PMS guest lifecycle snapshot.",
    parameters: { type: "object", properties: {}, additionalProperties: false },
  },
  hotel_telegram_send_message: {
    description: "Send a staff-facing Telegram message through the Hotel bot. Never sends guest messages.",
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
  hotel_memory_log: {
    description: "Append one concise Hotel operations memory line.",
    parameters: {
      type: "object",
      properties: { content: { type: "string" } },
      required: ["content"],
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
  id: "hotel-pms",
  name: "Hotel PMS Tools",
  description: "Read-only PMS tools and staff notifications for the Hotel operations agent.",
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
