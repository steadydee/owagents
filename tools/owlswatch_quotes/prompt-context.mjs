import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// This is a fixed, deployed instruction file, never a model-selected path.
export function createQuotePromptHook(workspace) {
  const skill = readFileSync(resolve(workspace, "skills/quote-draft/SKILL.md"), "utf8");
  if (!skill.includes("# Quote Draft Skill") || !skill.includes("owlswatch_quote_prepare")) {
    throw new Error("Cotiza quote skill is missing or invalid; repair the deployment.");
  }
  const appendSystemContext = [
    "# Cotiza quote workflow (already loaded by the trusted plugin)",
    "The complete quote-draft SKILL.md is included below. This satisfies skill loading.",
    "Do not call read or try to load instructions again: filesystem tools are intentionally unavailable.",
    "Use only the tools actually supplied in this turn. If one is unavailable, report that step as incomplete; do not invent a replacement tool.",
    skill,
  ].join("\n\n");
  return (_event, ctx) => ctx?.agentId === "cotiza" ? { appendSystemContext } : undefined;
}
