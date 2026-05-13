import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { spawn } from "node:child_process";

const SERVER = "/Users/agent/.openclaw/workspace-owlswatch-correo/tools/owlswatch_email/server.py";
const BASE_ENV = {
  OWLSWATCH_EMAIL_WORKSPACE: "/Users/agent/.openclaw/workspace-owlswatch-correo",
  OPENCLAW_CONFIG_PATH: "/Users/agent/.openclaw-owlswatch/openclaw.json"
};

const toolSchemas = {
  owlswatch_email_search_recent_threads: {
    description: "Search recent Owl's Watch Gmail threads read-only.",
    parameters: {
      type: "object",
      properties: {
        hours: { type: "integer", minimum: 1, maximum: 168 },
        query: { type: ["string", "null"] },
        maxResults: { type: "integer", minimum: 1, maximum: 25 }
      },
      additionalProperties: false
    }
  },
  owlswatch_email_search_unanswered_threads: {
    description: "Find recent Gmail threads whose latest meaningful message appears external/unanswered.",
    parameters: {
      type: "object",
      properties: {
        days: { type: "integer", minimum: 1, maximum: 30 },
        query: { type: ["string", "null"] },
        maxResults: { type: "integer", minimum: 1, maximum: 50 }
      },
      additionalProperties: false
    }
  },
  owlswatch_email_read_thread: {
    description: "Read one Owl's Watch Gmail thread read-only.",
    parameters: {
      type: "object",
      properties: { threadId: { type: "string" } },
      required: ["threadId"],
      additionalProperties: false
    }
  },
  owlswatch_email_resolve_gmail_url: {
    description: "Resolve a Gmail web URL to a readable Gmail thread when Gmail exposes a compatible API id.",
    parameters: {
      type: "object",
      properties: { url: { type: "string" } },
      required: ["url"],
      additionalProperties: false
    }
  },
  owlswatch_luna_get_email_response_context: {
    description: "Fetch approved guest-shareable Luna context for an email response.",
    parameters: {
      type: "object",
      properties: {
        clientQuestion: { type: "string" },
        language: { type: ["string", "null"] },
        topics: { type: "array", items: { type: "string" } },
        factLimit: { type: "integer" },
        blockLimit: { type: "integer" },
        mediaLimit: { type: "integer" }
      },
      required: ["clientQuestion"],
      additionalProperties: false
    }
  },
  owlswatch_email_submit_operations_intake: {
    description: "Submit an email draft task to Operations Email Desk. Requires EMAIL_AGENT_API_TOKEN. Does not send email.",
    parameters: {
      type: "object",
      properties: { payload: { type: "object", additionalProperties: true } },
      required: ["payload"],
      additionalProperties: false
    }
  },
  owlswatch_email_submit_scan_run: {
    description: "Submit a daily/recent/unanswered email scan summary to Operations Email Desk.",
    parameters: {
      type: "object",
      properties: { payload: { type: "object", additionalProperties: true } },
      required: ["payload"],
      additionalProperties: false
    }
  },
  owlswatch_email_upsert_task: {
    description: "Create or update a durable local email draft/review task.",
    parameters: {
      type: "object",
      properties: { task: { type: "object", additionalProperties: true } },
      required: ["task"],
      additionalProperties: false
    }
  },
  owlswatch_email_list_open_tasks: {
    description: "List durable local email tasks needing review or summary.",
    parameters: {
      type: "object",
      properties: {
        statuses: { type: "array", items: { type: "string" } },
        limit: { type: "integer", minimum: 1, maximum: 100 }
      },
      additionalProperties: false
    }
  },
  owlswatch_email_create_gmail_draft: {
    description: "Create a Gmail draft in the original thread when compose scope is explicitly enabled. Never sends.",
    parameters: {
      type: "object",
      properties: {
        threadId: { type: "string" },
        to: { type: "string" },
        subject: { type: "string" },
        body: { type: "string" },
        inReplyTo: { type: ["string", "null"] }
      },
      required: ["threadId", "to", "subject", "body"],
      additionalProperties: false
    }
  },
  owlswatch_email_send_telegram_message: {
    description: "Send an email-agent Telegram notification to the configured Owl's Watch ops chat/topic.",
    parameters: {
      type: "object",
      properties: {
        text: { type: "string" },
        chat_id: { type: ["string", "number", "null"] },
        message_thread_id: { type: ["string", "number", "null"] }
      },
      required: ["text"],
      additionalProperties: false
    }
  },
  owlswatch_email_memory_log: {
    description: "Append one concise Correo memory line.",
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
  id: "owlswatch-email",
  name: "Owl's Watch Email Tools",
  description: "Narrow first-class tools for Correo email triage and draft preparation.",
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
