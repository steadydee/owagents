// These values come from OpenClaw's tool factory/command context, not tool args.
// Missing context fails closed; never recover an identity from prompt text.
import { randomUUID } from "node:crypto";

export function hostContext(ctx, { command = false, now = Date.now() } = {}) {
  const channel = command ? (ctx.channelId ?? ctx.channel) : (ctx.deliveryContext?.channel ?? ctx.messageChannel);
  const sender = command ? ctx.senderId : ctx.requesterSenderId;
  const rawRoute = command ? (ctx.from ?? ctx.to) : ctx.deliveryContext?.to;
  const route = /^(?:telegram:)?(?:group:)?(-?\d{1,20})(?::(?:topic|thread):(\d+))?$/.exec(String(rawRoute ?? ""));
  const thread = command ? ctx.messageThreadId : ctx.deliveryContext?.threadId;
  if (channel !== "telegram" || ctx.agentId !== "hotel" || !/^\d{1,20}$/.test(String(sender ?? "")) || !route || !ctx.sessionKey) return null;
  if (route[2] && thread != null && route[2] !== String(thread)) return null;
  if (thread != null && !/^\d+$/.test(String(thread))) return null;
  if (command && ctx.isAuthorizedSender !== true) return null;
  return {
    channel, agentId: "hotel", senderId: String(sender), chatId: route[1],
    threadId: String(thread ?? route[2] ?? ""), sessionKey: ctx.sessionKey,
    accountId: ctx.accountId ?? ctx.agentAccountId ?? ctx.deliveryContext?.accountId ?? "default",
    ...(command ? {
      source: "native_command", authorized: true, approvedAt: now / 1000,
      approvalEventId: randomUUID(),
    } : { source: "tool_context" }),
  };
}

export function confirmationReply(result) {
  if (!result?.ok) return result?.error?.message ?? "No pude verificar la confirmación. Revisa la reserva en PMS.";
  const url = result.reservation?.pmsUrl;
  return `Reserva creada en PMS.${url ? `\n${url}` : ""}`;
}
