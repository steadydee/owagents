import { readFileSync } from "node:fs";
import { resolve } from "node:path";

function numericSenderId(value) {
  if (typeof value === "number" && !Number.isSafeInteger(value)) return undefined;
  if (typeof value !== "string" && typeof value !== "number") return undefined;
  const id = String(value);
  return /^[1-9]\d{0,19}$/.test(id) ? id : undefined;
}

// Only host-supplied current-turn context and configured numeric IDs count.
// Display names, message text, history, owner claims, and open topics do not.
export function resolveReceiptSenderAuthorization(ctx, config = {}) {
  if (ctx?.agentId !== "cuenta") return { authorized: false, reason: "wrong_agent" };
  const channels = [ctx.channel, ctx.messageProvider].filter((value) => value != null && value !== "");
  if (!channels.length || channels.some((value) => value !== "telegram")) {
    return { authorized: false, reason: "unverified_channel" };
  }
  const senderId = numericSenderId(ctx.senderId);
  if (!senderId) return { authorized: false, reason: "missing_sender" };
  const channelSenderId = ctx.channelContext?.sender?.id;
  if (channelSenderId != null && numericSenderId(channelSenderId) !== senderId) {
    return { authorized: false, reason: "conflicting_sender" };
  }
  const telegram = config?.channels?.telegram;
  const configuredIds = [telegram?.allowFrom, telegram?.groupAllowFrom]
    .flatMap((entries) => Array.isArray(entries) ? entries : [])
    .map(numericSenderId)
    .filter(Boolean);
  return configuredIds.includes(senderId)
    ? { authorized: true, reason: "configured_sender" }
    : { authorized: false, reason: "sender_not_configured" };
}

// Read only our deployed, versioned skill. Never accept a path from the model.
export function createReceiptPromptHook(workspace, config = {}) {
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
  return (_event, ctx) => {
    if (ctx?.agentId !== "cuenta") return undefined;
    const authorization = resolveReceiptSenderAuthorization(ctx, config);
    const currentSenderContext = authorization.authorized
      ? "The CURRENT Telegram sender is verified for receipt intake: the host-supplied sender ID exactly matches a configured numeric Telegram sender allowlist entry. Process this sender's receipt using the workflow below. Do not ask them to prove their name or refuse because of a display name, a previous sender, or an earlier refusal."
      : "The trusted plugin could not verify the CURRENT sender against the configured numeric Telegram sender allowlist. Do not download, upload, extract, or create an expense for this turn. Briefly say that this account's receipt-intake authorization could not be verified and needs an operator check. Do not guess the sender's identity, claim that only named people may submit, or treat group/topic membership or a prior sender's authorization as permission.";
    return {
      appendSystemContext: [appendSystemContext, "# Current receipt sender authorization", currentSenderContext].join("\n\n"),
    };
  };
}
