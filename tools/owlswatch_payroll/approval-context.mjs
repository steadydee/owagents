import { randomUUID } from "node:crypto";

// Only call with OpenClaw's host-owned factory/command context. User text and
// tool arguments must never be passed here. The server separately checks the
// configured sender/route allowlists and the pending action's complete binding.
const ID = /^-?[1-9]\d{0,19}$/;
const SENDER = /^[1-9]\d{0,19}$/;
const ACCOUNT = /^[a-zA-Z0-9_-]{1,64}$/;
const ROUTE = /^(?:telegram:)?(?:group:)?(-?[1-9]\d{0,19})(?::(?:topic|thread):([1-9]\d{0,19}))?$/;

function route(value) {
  const match = ROUTE.exec(String(value ?? ""));
  return match ? { chatId: match[1], threadId: match[2] ?? "" } : null;
}

function nonconflicting(values) {
  const present = values.filter(value => value !== undefined && value !== null && value !== "").map(String);
  return present.length && present.every(value => value === present[0]) ? present[0] : null;
}

export function hostContext(ctx, { command = false, now = Date.now() } = {}) {
  if (!ctx || typeof ctx !== "object" || ctx.agentId !== "nomina") return null;
  const channel = nonconflicting(command ? [ctx.channelId, ctx.channel] : [ctx.deliveryContext?.channel, ctx.messageChannel]);
  const senderId = String(command ? ctx.senderId ?? "" : ctx.requesterSenderId ?? "");
  const accountId = nonconflicting(command ? [ctx.accountId] : [ctx.deliveryContext?.accountId, ctx.agentAccountId]);
  const sessionKey = ctx.sessionKey;
  if (channel !== "telegram" || !SENDER.test(senderId) || !ACCOUNT.test(accountId ?? "")) return null;
  if (typeof sessionKey !== "string" || !sessionKey.length || sessionKey.length > 512 || /[\x00-\x1F\x7F]/.test(sessionKey)) return null;
  if (command && ctx.isAuthorizedSender !== true) return null;
  const target = route(command ? ctx.from ?? ctx.to : ctx.deliveryContext?.to);
  if (!target || !ID.test(target.chatId)) return null;
  const rawThread = command ? ctx.messageThreadId : ctx.deliveryContext?.threadId;
  const threadId = nonconflicting([target.threadId, rawThread]) ?? "";
  if (rawThread !== undefined && rawThread !== null && !SENDER.test(String(rawThread))) return null;
  if (target.threadId && rawThread !== undefined && rawThread !== null && target.threadId !== String(rawThread)) return null;
  // Telegram supplies both from (including topic) and to (parent chat). Any
  // supplied destination must agree; a malformed secondary route fails closed.
  if (command && ctx.to !== undefined && ctx.to !== null) {
    const destination = route(ctx.to);
    if (!destination || destination.chatId !== target.chatId || (destination.threadId && destination.threadId !== threadId)) return null;
  }
  return {
    channel, agentId: "nomina", senderId, accountId, chatId: target.chatId,
    threadId, sessionKey,
    ...(command ? {
      source: "native_command", authorized: true, approvedAt: now / 1000,
      approvalEventId: randomUUID(),
    } : { source: "tool_context" }),
  };
}

export function confirmationToken(value) {
  const token = typeof value === "string" ? value.trim() : "";
  return /^[A-F0-9]{16,32}$/.test(token) ? token : null;
}

export function confirmationReply(result) {
  // summary/error.message are server-created, privacy-filtered display fields.
  // Do not serialize the result: it can contain internal audit identifiers.
  const value = result?.ok === true ? result.summary : result?.error?.message;
  if (typeof value === "string" && value.trim() && value.length <= 1200) {
    return value.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, "");
  }
  return result?.ok === true
    ? "Confirmación registrada. Consulta el estado actualizado de la nómina."
    : "No pude verificar esta confirmación. Consulta el estado de la nómina y conserva la misma referencia para revisar el resultado.";
}

export function reviewReply(result) {
  // The native Telegram delivery layer splits long text into bounded messages.
  // This server-owned preview is intentionally more detailed than ordinary chat.
  if (result?.ok === true && typeof result.summary === "string" && result.summary.trim() && result.summary.length <= 60000) {
    return result.summary.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, "");
  }
  return result?.ok === false ? confirmationReply(result)
    : "No pude mostrar la revisión completa. No confirmes esta referencia; solicita una revisión que pueda verificarse en Nómina.";
}
