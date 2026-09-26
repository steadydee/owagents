import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createCobrosGuards } from "./run-guards.mjs";
import { callPythonTool } from "./tool-bridge.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const WORKSPACE = resolve(TOOL_DIR, "../..");
const SERVER = resolve(TOOL_DIR, "server.py");
const BASE_ENV = {
  OWLSWATCH_COBROS_WORKSPACE: WORKSPACE
};

const toolSchemas = {
  owlswatch_cobros_search_gmail_threads: {
    description: "Search read-only Owl's Watch Gmail for cuenta de cobro/accounting requests.",
    parameters: {
      type: "object",
      properties: {
        query: { type: ["string", "null"] },
        maxResults: { type: "integer", minimum: 1, maximum: 5 },
        purpose: { type: "string", enum: ["accounting", "billing_identity"] }
      },
      additionalProperties: false
    }
  },
  owlswatch_cobros_read_gmail_thread: {
    description: "Read one Owl's Watch Gmail thread for cuenta de cobro drafting.",
    parameters: {
      type: "object",
      properties: { threadId: { type: "string" } },
      required: ["threadId"],
      additionalProperties: false
    }
  },
  owlswatch_cobros_prepare: {
    description: "Extract and validate cuenta de cobro fields. Does not create documents.",
    parameters: {
      type: "object",
      properties: {
        raw_text: { type: ["string", "null"] },
        thread: { type: ["object", "null"], additionalProperties: true },
        source_metadata: { type: ["object", "null"], additionalProperties: true },
        human_override: { type: "boolean" },
        override_fields: { type: ["object", "null"], additionalProperties: true }
      },
      additionalProperties: false
    }
  },
  owlswatch_cobros_create_packet: {
    description: "Create Google Doc cuenta de cobro and exported PDF from a ready prepared result.",
    parameters: {
      type: "object",
      properties: { prepared: { type: "object", additionalProperties: true } },
      required: ["prepared"],
      additionalProperties: false
    }
  },
  owlswatch_cobros_create_gmail_draft: {
    description: "Create a Gmail draft reply with the cuenta de cobro PDF attached. Never sends.",
    parameters: {
      type: "object",
      properties: {
        prepared: { type: "object", additionalProperties: true },
        packet: { type: "object", additionalProperties: true },
        thread: { type: ["object", "null"], additionalProperties: true },
        threadId: { type: ["string", "null"] },
        to: { type: ["string", "null"] },
        subject: { type: ["string", "null"] },
        body: { type: ["string", "null"] },
        inReplyTo: { type: ["string", "null"] },
        sourceMessageId: { type: ["string", "null"] },
        sourceSummary: { type: ["string", "null"] }
      },
      required: ["prepared", "packet"],
      additionalProperties: false
    }
  },
  owlswatch_cobros_send_telegram_message: {
    description: "Send a short Cobros Telegram notification to the configured Owl's Watch topic.",
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
  owlswatch_cobros_memory_log: {
    description: "Append one concise Cobros memory line.",
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

export default definePluginEntry({
  id: "owlswatch-cobros",
  name: "Owl's Watch Cobros Tools",
  description: "Narrow tools for Cobros cuenta de cobro drafting.",
  register(api) {
    const guards = createCobrosGuards(WORKSPACE);
    api.on("before_prompt_build", guards.prompt);
    api.on("before_tool_call", guards.beforeTool);
    api.on("agent_end", guards.end);
    for (const [name, spec] of Object.entries(toolSchemas)) {
      api.registerTool({
        name,
        label: name,
        description: spec.description,
        parameters: spec.parameters,
        execute: async (_toolCallId, rawParams) => jsonResult(await callPythonTool(SERVER, { ...process.env, ...BASE_ENV }, name, rawParams))
      }, { name });
    }
  }
});
