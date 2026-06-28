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
  hotel_pms_get_tomorrow_summary: {
    description: "Return enriched PMS arrivals, departures, and stayovers for tomorrow or a supplied YYYY-MM-DD date.",
    parameters: {
      type: "object",
      properties: { date: { type: ["string", "null"] } },
      additionalProperties: false,
    },
  },
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
  hotel_pms_list_departures: {
    description: "List PMS departures for a supplied date or today.",
    parameters: {
      type: "object",
      properties: { date: { type: ["string", "null"] } },
      additionalProperties: false,
    },
  },
  hotel_pms_list_in_house: {
    description: "List PMS in-house reservations for a supplied date or today.",
    parameters: {
      type: "object",
      properties: { date: { type: ["string", "null"] } },
      additionalProperties: false,
    },
  },
  hotel_pms_list_reservations: {
    description: "Search/list PMS reservations with staff-safe operational fields and no finance amounts.",
    parameters: {
      type: "object",
      properties: {
        search: { type: ["string", "null"] },
        source: { type: ["string", "null"] },
        status: { type: ["string", "null"] },
        dateFrom: { type: ["string", "null"] },
        dateTo: { type: ["string", "null"] },
        limit: { type: ["integer", "null"] },
      },
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
    description: "Get read-only staff-safe PMS reservation context, checklist, and operational metadata.",
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
  hotel_pms_list_booking_revisions: {
    description: "List PMS booking/channel revision inbox rows.",
    parameters: {
      type: "object",
      properties: {
        processingStatus: { type: ["string", "null"] },
        ackStatus: { type: ["string", "null"] },
      },
      additionalProperties: false,
    },
  },
  hotel_pms_list_sync_events: {
    description: "List PMS sync/channel events with optional status, direction, or resource type filters.",
    parameters: {
      type: "object",
      properties: {
        status: { type: ["string", "null"] },
        direction: { type: ["string", "null"] },
        resourceType: { type: ["string", "null"] },
      },
      additionalProperties: false,
    },
  },
  hotel_pms_get_mapping_status: {
    description: "Get PMS channel/entity mapping status.",
    parameters: {
      type: "object",
      properties: { entityType: { type: ["string", "null"] } },
      additionalProperties: false,
    },
  },
  hotel_pms_get_ari_outbox_health: {
    description: "Get PMS channel manager outbound queue health.",
    parameters: { type: "object", properties: {}, additionalProperties: false },
  },
  hotel_pms_prepare_reservation: {
    description: "Prepare and validate a PMS reservation from normalized staff intent. No PMS reservation is created.",
    parameters: {
      type: "object",
      properties: {
        bookingType: { type: ["string", "null"] },
        guestName: { type: ["string", "null"] },
        guestEmail: { type: ["string", "null"] },
        guestPhone: { type: ["string", "null"] },
        operatorName: { type: ["string", "null"] },
        sourceText: { type: ["string", "null"] },
        source: { type: ["string", "null"] },
        commercialTrack: { type: ["string", "null"] },
        payerResponsibility: { type: ["string", "null"] },
        sourceReference: { type: ["string", "null"] },
        arrivalDate: { type: ["string", "null"] },
        departureDate: { type: ["string", "null"] },
        visitDate: { type: ["string", "null"] },
        adultsCount: { type: ["integer", "null"] },
        childrenCount: { type: ["integer", "null"] },
        infantsCount: { type: ["integer", "null"] },
        unitAllocations: {
          type: ["array", "null"],
          items: {
            type: "object",
            properties: {
              unitCode: { type: ["string", "null"] },
              quantity: { type: ["integer", "null"] },
              label: { type: ["string", "null"] },
            },
            additionalProperties: false,
          },
        },
        expectedArrivalTime: { type: ["string", "null"] },
        transportRequested: { type: ["boolean", "null"] },
        dietaryNotes: { type: ["string", "null"] },
        specialRequests: { type: ["string", "null"] },
        internalNotes: { type: ["string", "null"] },
        linkedActivities: {
          type: ["array", "null"],
          items: {
            type: "object",
            properties: {
              bookingType: { type: ["string", "null"] },
              date: { type: ["string", "null"] },
              participants: { type: ["integer", "null"] },
              notes: { type: ["string", "null"] },
            },
            additionalProperties: false,
          },
        },
        sourceMetadata: { type: ["object", "null"], additionalProperties: true },
      },
      additionalProperties: false,
    },
  },
  hotel_pms_create_reservation: {
    description: "Create a PMS reservation from a pending PMS-prepared draft after staff replies si, or from a legacy confirmation code.",
    parameters: {
      type: "object",
      properties: {
        pendingId: { type: ["string", "null"] },
        confirmationText: { type: ["string", "null"] },
        confirmationCode: { type: ["string", "null"] },
        idempotencyKey: { type: ["string", "null"] },
        sourceMetadata: { type: ["object", "null"], additionalProperties: true },
      },
      additionalProperties: false,
    },
  },
  hotel_registro_get_by_reservation: {
    description: "Read the Registro record for a PMS reservation without exposing document bytes or fetch tokens.",
    parameters: {
      type: "object",
      properties: { reservationId: { type: "string" } },
      required: ["reservationId"],
      additionalProperties: false,
    },
  },
  hotel_registro_list_guests: {
    description: "List structured Registro guests for a registration.",
    parameters: {
      type: "object",
      properties: { registrationId: { type: "string" } },
      required: ["registrationId"],
      additionalProperties: false,
    },
  },
  hotel_registro_list_documents: {
    description: "List Registro document metadata for a registration or guest without exposing document bytes or fetch tokens.",
    parameters: {
      type: "object",
      properties: {
        registrationId: { type: "string" },
        registrationGuestId: { type: ["string", "null"] },
      },
      required: ["registrationId"],
      additionalProperties: false,
    },
  },
  hotel_registro_extract_reservation: {
    description: "Fetch each guest document through scoped PMS Registro tokens, extract identity fields with vision, and record guest-level extraction results in PMS.",
    parameters: {
      type: "object",
      properties: {
        reservationId: { type: "string" },
        record: { type: ["boolean", "null"] },
      },
      required: ["reservationId"],
      additionalProperties: false,
    },
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
  description: "PMS read tools, guarded reservation creation, and staff notifications for the Hotel operations agent.",
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
