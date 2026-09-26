import { readFileSync } from "node:fs";
import { resolve } from "node:path";

export function createCobrosGuards(workspace) {
  const skill = readFileSync(resolve(workspace, "skills/cuenta-cobro/SKILL.md"), "utf8");
  if (!skill.includes("owlswatch_cobros_prepare")) throw new Error("Cobros skill missing or invalid");
  const runs = new Map();
  const research = new Set(["owlswatch_cobros_search_gmail_threads", "owlswatch_cobros_read_gmail_thread"]);
  return {
    prompt(_event, ctx) {
      if (ctx.agentId !== "cobros") return;
      return { appendSystemContext: [
        "# Cobros workflow (already loaded by the trusted plugin)",
        "Do not call read or message or try to load instructions again. The complete skill follows.",
        "Gmail research permits at most 3 targeted searches and 3 reads per request.",
        "If information remains missing or lookup fails, stop and ask one concise question. Do not keep searching.",
        "Never invent a NIT, payee or amount. Never claim an unconfirmed write succeeded.", skill,
      ].join("\n\n") };
    },
    beforeTool(event, ctx) {
      if (ctx.agentId !== "cobros" || !research.has(event.toolName)) return;
      const stop = reason => ({ block: true, blockReason: `${reason} Stop research and ask for the missing information. Do not create documents without required fields.` });
      const id = ctx.runId || event.runId;
      if (!id) return stop("Trusted request identity unavailable.");
      // Prompt rebuilds must not reset the request budget.
      if (!runs.has(id) && runs.size >= 512) return stop("Research capacity reached.");
      const counts = runs.get(id) || {};
      runs.set(id, counts);
      if ((counts[event.toolName] || 0) >= 3) return stop("Gmail research limit reached; required data remains unverified.");
      counts[event.toolName] = (counts[event.toolName] || 0) + 1;
    },
    end(_event, ctx) { if (ctx.agentId === "cobros" && ctx.runId) runs.delete(ctx.runId); },
  };
}
