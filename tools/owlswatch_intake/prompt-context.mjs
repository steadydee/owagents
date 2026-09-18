import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// Read only our deployed, versioned skill. Never accept a path from the model.
export function createReceiptPromptHook(workspace) {
  const skill = readFileSync(resolve(workspace, "skills/intake-receipt/SKILL.md"), "utf8");
  if (!skill.includes("# intake-receipt") || !skill.includes("owlswatch_operations_create_expense_draft")) {
    throw new Error("Cuenta receipt skill is missing or invalid; deployment must be repaired.");
  }
  const appendSystemContext = [
    "# Cuenta receipt workflow (already loaded by the trusted plugin)",
    "The complete intake-receipt SKILL.md is included below. This satisfies skill loading.",
    "Do not call read or try to load instructions again: filesystem tools are intentionally unavailable.",
    "Follow this workflow using only the tools actually supplied in this turn.",
    "If a tool is unavailable, do not retry it or claim the expense was saved.",
    skill,
  ].join("\n\n");
  return (_event, ctx) => ctx.agentId === "cuenta" ? { appendSystemContext } : undefined;
}
