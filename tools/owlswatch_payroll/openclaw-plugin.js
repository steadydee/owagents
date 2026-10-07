import { definePluginEntry } from "openclaw/plugin-sdk/core";
import { sendDurableMessageBatch } from "openclaw/plugin-sdk/channel-outbound";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { hostContext } from "./approval-context.mjs";
import { callPython, trustedEnvironment, toolTimeout } from "./tool-bridge.mjs";
import { nativeHandlers, addReviewButtons, addReportButtons } from "./native-ui.mjs";
import { registerDeliveryHooks, deliverFinalReply } from "./delivery-journal.mjs";

const TOOL_DIR = dirname(fileURLToPath(import.meta.url));
const SERVER = resolve(TOOL_DIR, "server.py");
const WORKSPACE = process.env.OWLSWATCH_PAYROLL_WORKSPACE || resolve(TOOL_DIR, "../..");
const PYTHON = process.env.OWLSWATCH_PAYROLL_PYTHON || "python3";
const ENV = { ...process.env, OWLSWATCH_PAYROLL_WORKSPACE: WORKSPACE, PYTHONDONTWRITEBYTECODE: "1" };

function jsonResult(value) {
  return { content: [{ type: "text", text: JSON.stringify(value) }],
    details: { structuredContent: value, status: value?.ok === false ? "error" : "ok" } };
}

export default definePluginEntry({
  id: "owlswatch-payroll", name: "Owl's Watch Nómina Tools",
  description: "Reviewed payroll drafts, loan ledger, and native human confirmations.",
  register(api) {
    const catalog = JSON.parse(execFileSync(PYTHON, [SERVER, "catalog"], {
      env: trustedEnvironment(ENV, null), encoding: "utf8", timeout: 10000, maxBuffer: 200000,
    }));
    // In a checkout the trusted skill lives under openclaw/agents; deploys place
    // the same versioned file directly in the specialist workspace.
    let skill;
    for (const path of [resolve(WORKSPACE, "skills/payroll/SKILL.md"), resolve(TOOL_DIR, "../../openclaw/agents/nomina/skills/payroll/SKILL.md")]) {
      try { skill = readFileSync(path, "utf8"); break; } catch (error) { if (error.code !== "ENOENT") throw error; }
    }
    if (!skill) throw new Error("Nómina trusted workflow is missing; restore the versioned payroll skill.");
    api.on("before_prompt_build", (_event, ctx) => ctx.agentId === "nomina"
      ? { prependSystemContext: `Follow this trusted Nómina workflow for every payroll request:\n${skill}` }
      : undefined);
    const journal = registerDeliveryHooks(api, WORKSPACE);
    api.on("reply_payload_sending", async (event, ctx) => {
      if (!String(event.sessionKey ?? ctx.sessionKey ?? "").startsWith("agent:nomina:") || event.kind !== "final") return;
      const payload = addReportButtons(addReviewButtons(event.payload));
      return deliverFinalReply({ ...event, payload }, ctx, journal, (trusted, final) => sendDurableMessageBatch({
        cfg: api.config, channel: "telegram", to: trusted.chatId, accountId: trusted.accountId,
        threadId: trusted.threadId || undefined,
        replyToId: trusted.messageId, session: { key: trusted.sessionKey, agentId: "nomina" },
        payloads: [final], durability: "required",
      }));
    });
    const handlers = nativeHandlers({ workspace: WORKSPACE,
      call: (command, name, args, trusted) => callPython({ server: SERVER, env: ENV, python: PYTHON, command, name, args, trusted }),
      observe: journal.native,
      send: (ctx, trusted, payload, options = {}) => sendDurableMessageBatch({
        cfg: ctx.config, channel: "telegram", to: trusted.chatId, accountId: trusted.accountId,
        threadId: trusted.threadId || undefined, session: { key: trusted.sessionKey, agentId: "nomina" },
        payloads: [payload], durability: "required", forceDocument: Boolean(payload.mediaUrl),
        onPayload: options.onPayload,
      }),
    });
    for (const [name, spec] of Object.entries(catalog)) {
      if (!/^nomina_[a-z_]+$/.test(name) || typeof spec.description !== "string" || !spec.parameters) throw new Error("Invalid Nómina tool catalog.");
      api.registerTool(ctx => {
        if (ctx.agentId !== "nomina") return null;
        return { name, label: name, description: spec.description, parameters: spec.parameters,
          execute: async (toolCallId, rawParams) => {
            const trusted = hostContext(ctx);
            // The SDK has no inbound Telegram message ID on tool contexts.
            // This is audit metadata, never an approval or dedupe substitute.
            if (trusted && /^[A-Za-z0-9_.:-]{1,180}$/.test(String(toolCallId ?? ""))) trusted.sourceEventId = `tool-call:${toolCallId}`;
            return jsonResult(await callPython({ server: SERVER, env: ENV, python: PYTHON,
              name, args: rawParams, trusted, timeoutMs: toolTimeout(name) }));
          } };
      }, { name });
    }
    api.registerCommand({
      name: "confirmar_nomina", description: "Confirma la revisión exacta de nómina desde la misma cuenta y conversación.",
      channels: ["telegram"], acceptsArgs: true, requireAuth: true,
      handler: handlers.confirm,
    });
    api.registerCommand({
      name: "revisar_nomina", description: "Muestra la revisión exacta antes de confirmar una operación de nómina.",
      channels: ["telegram"], acceptsArgs: true, requireAuth: true,
      handler: handlers.review,
    });
    api.registerCommand({
      name: "informe_nomina", description: "Descarga un informe privado de una nómina finalizada.",
      channels: ["telegram"], acceptsArgs: true, requireAuth: true, handler: handlers.report,
    });
  },
});
