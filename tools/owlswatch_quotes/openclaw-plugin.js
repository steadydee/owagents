import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { spawn } from "node:child_process";

const SERVER = "/Users/agent/.openclaw/workspace-owlswatch-cotiza/tools/owlswatch_quotes/server.py";
const BASE_ENV = {
  OWLSWATCH_COTIZA_WORKSPACE: "/Users/agent/.openclaw/workspace-owlswatch-cotiza",
  OPENCLAW_CONFIG_PATH: "/Users/agent/.openclaw-owlswatch/openclaw.json"
};

const toolSchemas = {
  owlswatch_gmail_search_quote_threads: {
    description: "Search read-only Owl's Watch Gmail quote threads by keywords or a Gmail browser URL.",
    parameters: {
      type: "object",
      properties: {
        query: { type: "string" },
        from: { type: ["string", "null"] },
        after: { type: ["string", "null"] },
        before: { type: ["string", "null"] },
        label: { type: ["string", "null"] },
        maxResults: { type: "integer", minimum: 1, maximum: 10 }
      },
      required: ["query"],
      additionalProperties: false
    }
  },
  owlswatch_gmail_read_thread: {
    description: "Read one Owl's Watch Gmail quote thread by id.",
    parameters: {
      type: "object",
      properties: { threadId: { type: "string" } },
      required: ["threadId"],
      additionalProperties: false
    }
  },
  owlswatch_quote_prepare: {
    description: "Prepare a quote from raw request text, normalize defaults, validate missing info, and calculate a pricing preview.",
    parameters: {
      type: "object",
      properties: {
        raw_text: { type: "string" },
        source_metadata: { type: "object", additionalProperties: true },
        prior_context: { type: ["object", "null"], additionalProperties: true },
        user_overrides: { type: ["object", "null"], additionalProperties: true },
        parsed_intent: { type: ["object", "null"], additionalProperties: true },
        mode: { type: ["string", "null"] }
      },
      required: ["raw_text"],
      additionalProperties: false
    }
  },
  owlswatch_quote_calculate: {
    description: "Calculate a quote using the Operations quote pricing endpoint.",
    parameters: {
      type: "object",
      properties: { payload: { type: "object", additionalProperties: true } },
      required: ["payload"],
      additionalProperties: false
    }
  },
  owlswatch_quote_create_draft: {
    description: "Create an Operations quote draft and Drive sheet from a prepared quote. Set redo=true to create a fresh draft from an already-drafted source.",
    parameters: {
      type: "object",
      properties: {
        prepared_quote: { type: "object", additionalProperties: true },
        source_metadata: { type: "object", additionalProperties: true },
        idempotency_key: { type: ["string", "null"] },
        redo: { type: ["boolean", "null"] },
        payload: { type: "object", additionalProperties: true }
      },
      additionalProperties: false
    }
  },
  owlswatch_quote_revise_draft: {
    description: "Create a revised quote draft and Drive sheet from an existing draft plus a simple instruction such as remove 2 lunches.",
    parameters: {
      type: "object",
      properties: {
        quote_ref: { type: "string" },
        quoteNumber: { type: "string" },
        quoteId: { type: "string" },
        instruction: { type: "string" },
        source_metadata: { type: "object", additionalProperties: true }
      },
      required: ["instruction"],
      additionalProperties: false
    }
  },
  owlswatch_quote_update_drive: {
    description: "Patch an Operations quote row with its Google Drive draft link.",
    parameters: {
      type: "object",
      properties: {
        quoteId: { type: "string" },
        driveFileId: { type: "string" },
        driveSheetUrl: { type: "string" }
      },
      required: ["quoteId", "driveFileId", "driveSheetUrl"],
      additionalProperties: false
    }
  },
  owlswatch_drive_create_quote_sheet: {
    description: "Create a Google Drive quote draft sheet in the configured folder.",
    parameters: {
      type: "object",
      properties: {
        quoteId: { type: "string" },
        quoteNumber: { type: "string" },
        agencyName: { type: ["string", "null"] },
        requesterName: { type: ["string", "null"] },
        clientName: { type: ["string", "null"] },
        arrivalDate: { type: ["string", "null"] },
        departureDate: { type: ["string", "null"] },
        guestCount: { type: ["integer", "null"], minimum: 0 },
        requestSummary: { type: ["string", "null"] },
        calculation: { type: "object", additionalProperties: true },
        assumptions: { type: "array", items: { type: "string" } },
        missingFields: { type: "array", items: { type: "string" } },
        replyDraft: { type: ["string", "null"] },
        sourceTrace: { type: "object", additionalProperties: true }
      },
      required: ["quoteId", "quoteNumber", "calculation"],
      additionalProperties: false
    }
  },
  owlswatch_cotiza_memory_log: {
    description: "Append one quote summary line to Cotiza memory.",
    parameters: {
      type: "object",
      properties: { content: { type: "string" } },
      required: ["content"],
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
  id: "owlswatch-quotes",
  name: "Owl's Watch Quote Tools",
  description: "Narrow first-class tools for Cotiza quote drafting.",
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
