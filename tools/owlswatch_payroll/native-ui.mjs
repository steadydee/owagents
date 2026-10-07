import { createHash } from "node:crypto";
import { realpathSync } from "node:fs";
import { relative, isAbsolute } from "node:path";
import { confirmationToken, confirmationReply, reviewReply, hostContext } from "./approval-context.mjs";

export function commandButton(label, command) {
  return { blocks: [{ type: "buttons", buttons: [{ label, action: { type: "command", command } }] }] };
}

export function addReviewButtons(payload) {
  if (!payload || payload.presentation || typeof payload.text !== "string") return payload;
  const matches = [...payload.text.matchAll(/\/revisar_nomina ([A-F0-9]{16})(?![A-F0-9])/g)];
  const tokens = [...new Set(matches.map(match => match[1]))];
  if (tokens.length !== 1) return payload;
  return { ...payload, presentation: commandButton("Revisar", `/revisar_nomina ${tokens[0]}`) };
}

export function verifiedReceiptIds(result) {
  if (result?.status !== "sent") return null;
  const ids = result.receipt?.platformMessageIds;
  return Array.isArray(ids) && ids.length > 0 && ids.length <= 100 &&
    ids.every(id => typeof id === "string" && /^[1-9][0-9]{0,19}$/.test(id)) ? ids : null;
}

const ACCESS_ERROR = "Esta cuenta o conversación no tiene acceso a Nómina.";
const DELIVERY_ERROR = "No pude verificar la entrega completa. No vuelvas a preparar el cambio: consulta su estado o abre la misma revisión. No he repetido ninguna operación.";

export function friendlyError(result) {
  const code = result?.error?.code;
  const messages = {
    CONFIRMATION_EXPIRED: "Esta revisión venció. Pídeme una revisión nueva; no se aplicó el cambio.",
    STALE_PREVIEW: "Los datos cambiaron después de preparar esta revisión. Pídeme una nueva antes de confirmar.",
    WRONG_APPROVER: "Esta revisión pertenece a otra cuenta o conversación. Ábrela desde donde la solicitaste.",
    UNAUTHORIZED: ACCESS_ERROR,
    REVIEW_REQUIRED: "Primero pulsa Revisar y espera a recibir todos los detalles. Después podrás confirmar.",
    REVIEW_DELIVERY_MISMATCH: "Los detalles cambiaron durante la entrega. Vuelve a pulsar Revisar antes de confirmar.",
    INVALID_CONFIRMATION: "No encontré esa revisión. Pídeme preparar el cambio de nuevo.",
    DRAFT_NOT_FINAL: "La nómina todavía es un borrador. Revisa y confirma su finalización antes de descargar el informe.",
  };
  return messages[code] ?? confirmationReply(result);
}

// send is the host's durable channel sender, not a model tool. Only this
// authenticated route may receive native reviews and private report files.
export function nativeHandlers({ call, send, workspace, observe = () => {} }) {
  const deliver = async (ctx, trusted, payload, stage, verifyText = false) => {
    let count = 0, unchanged = true;
    try {
      const result = await send(ctx, trusted, payload, { onPayload: effective => {
        count++;
        unchanged &&= effective.text === payload.text;
      } });
      if (verifyText && (count !== 1 || !unchanged)) {
        observe(trusted, stage, "content_changed");
        return { status: "failed" };
      }
      observe(trusted, stage, verifiedReceiptIds(result) ? "sent" : "uncertain");
      return result;
    } catch {
      observe(trusted, stage, "uncertain");
      return { status: "failed" };
    }
  };
  return {
    review: async ctx => {
      const trusted = hostContext(ctx, { command: true });
      if (!trusted) return { text: ACCESS_ERROR };
      const token = confirmationToken(ctx.args);
      if (!token) return { text: "Falta la referencia de la revisión. Pídeme preparar el cambio y pulsa el botón Revisar que te mostraré." };
      const result = await call("review", token, {}, trusted);
      if (!result?.ok) return { text: friendlyError(result) };
      if (result.result?.already_confirmed) return { text: reviewReply(result) };
      const text = reviewReply(result);
      const hash = createHash("sha256").update(text).digest("hex");
      if (hash !== result.sha256) return { text: "La revisión no se pudo verificar. No confirmes todavía; solicita una revisión nueva." };
      const sent = await deliver(ctx, trusted, { text }, "review", true);
      const ids = verifiedReceiptIds(sent);
      if (!ids) return { text: DELIVERY_ERROR };
      const recorded = await call("review-delivered", token, { sha256: hash, message_ids: ids }, trusted);
      if (!recorded?.ok) return { text: friendlyError(recorded) };
      // A separate control follows delivery of every review chunk. Never place
      // Confirmar on the first chunk of a partially delivered financial review.
      return { text: "Revisa los datos anteriores. Confirmar guarda únicamente el cambio mostrado; no hace transferencias bancarias.",
        presentation: commandButton("Confirmar", `/confirmar_nomina ${token}`) };
    },
    confirm: async ctx => {
      const trusted = hostContext(ctx, { command: true });
      if (!trusted) return { text: ACCESS_ERROR };
      const token = confirmationToken(ctx.args);
      if (!token) return { text: "Falta la referencia de la confirmación. Primero pulsa Revisar en el cambio preparado y después Confirmar." };
      // Persist the request before the local commit. Recovery may notify about
      // an uncertain reply, but must never replay a financial action itself.
      if (observe(trusted, "confirmation", "pending") !== true) return {
        text: "No pude guardar el registro de esta solicitud. No apliqué el cambio. Conserva la misma referencia y solicita ayuda antes de confirmar de nuevo.",
      };
      let result;
      try { result = await call("approve", token, {}, trusted); }
      catch { observe(trusted, "confirmation", "uncertain"); return { text: DELIVERY_ERROR }; }
      const sent = await deliver(ctx, trusted, { text: result?.ok ? confirmationReply(result) : friendlyError(result) }, "confirmation");
      return verifiedReceiptIds(sent) ? { suppressReply: true } : { text: DELIVERY_ERROR };
    },
    report: async ctx => {
      const trusted = hostContext(ctx, { command: true });
      if (!trusted) return { text: ACCESS_ERROR };
      const match = /^([A-Za-z0-9][A-Za-z0-9_-]{0,79}) (csv|html)$/.exec(String(ctx.args ?? "").trim());
      if (!match) return { text: "Pídeme el informe de una nómina finalizada. Te daré los botones Descargar CSV y Descargar HTML." };
      const result = await call("report", match[1], { format: match[2] }, trusted);
      if (!result?.ok) return { text: friendlyError(result) };
      let path;
      try {
        path = realpathSync(result.result.media_path);
        const rel = relative(realpathSync(workspace), path);
        if (isAbsolute(rel) || !/^exports\/[a-f0-9]{64}\/(payments\.csv|payroll\.html)$/.test(rel)) throw new Error();
      } catch { return { text: "El informe no pasó la verificación de archivo privado. No se adjuntó ningún archivo." }; }
      const sent = await deliver(ctx, trusted, { text: result.summary, mediaUrl: path }, "report");
      return verifiedReceiptIds(sent) ? { suppressReply: true } : { text: DELIVERY_ERROR };
    },
  };
}

export function addReportButtons(payload) {
  if (!payload || payload.presentation || typeof payload.text !== "string") return payload;
  const matches = [...payload.text.matchAll(/\/informe_nomina ([A-Za-z0-9][A-Za-z0-9_-]{0,79}) (csv|html)\b/g)];
  if (!matches.length || new Set(matches.map(match => match[1])).size !== 1) return payload;
  return { ...payload, presentation: { blocks: [{ type: "buttons", buttons:
    [...new Map(matches.map(match => [match[2], match])).values()].map(match => ({
      label: `Descargar ${match[2].toUpperCase()}`, action: { type: "command", command: match[0] },
    })) }] } };
}
