"""Approval captured by a native, authenticated command, never by model args."""
import datetime as dt
import json
import os
import re
from durable_state import StateError, fingerprint, locked_record


def trusted_context(approval=False):
    # The plugin replaces this environment variable for every invocation. It is
    # deliberately separate from the JSON model argument stream.
    try:
        context = json.loads(os.environ.get("HOTEL_TRUSTED_CONTEXT", "{}"))
    except ValueError:
        context = {}
    valid = (
        isinstance(context, dict) and context.get("channel") == "telegram"
        and context.get("agentId") == "hotel"
        and re.fullmatch(r"\d{1,20}", str(context.get("senderId", "")))
        and re.fullmatch(r"-?\d{1,20}", str(context.get("chatId", "")))
        and isinstance(context.get("sessionKey"), str) and context["sessionKey"]
    )
    if not valid:
        raise StateError("trusted_context_required", "No pude verificar quién solicita la reserva. Revísala y créala en PMS.")
    if approval:
        if context.get("source") != "native_command" or context.get("authorized") is not True:
            raise StateError("human_approval_required", "Confirma desde Telegram con el comando que acompaña la reserva, o revísala en PMS.")
        try:
            age = dt.datetime.now(dt.timezone.utc).timestamp() - float(context.get("approvedAt", 0))
        except (ValueError, TypeError):
            age = 999999
        if not 0 <= age <= 60 or not re.fullmatch(r"[a-f0-9-]{36}", str(context.get("approvalEventId", ""))):
            raise StateError("approval_expired", "La confirmación venció. Repite el comando de confirmación desde Telegram.")
    return context


def binding(context):
    return {key: str(context.get(key) or "") for key in ("senderId", "chatId", "threadId", "sessionKey", "accountId")}


def draft_hash(draft):
    return fingerprint({key: draft.get(key) for key in ("preparedToken", "confirmationCode", "requestPayload", "expiresAt", "approvalBinding")})


def execute_approved(root, pending_id, draft, context, approve, create):
    if not draft.get("approvalBinding") or binding(context) != draft["approvalBinding"]:
        raise StateError("approval_binding_mismatch", "Confirma en la misma conversación y con la misma cuenta que preparó la reserva, o revísala en PMS.")
    if draft_hash(draft) != draft.get("approvalPayloadHash"):
        raise StateError("prepared_draft_changed", "La reserva preparada cambió. Prepara una nueva antes de confirmar.")
    with locked_record(root, ["reservation", pending_id]) as (record, save):
        if record.get("payloadHash") and record["payloadHash"] != draft["approvalPayloadHash"]:
            raise StateError("prepared_draft_changed", "La reserva preparada cambió. Revísala en PMS.")
        if record.get("state") == "created":
            return record["result"]
        if record.get("state") == "creating":
            # PMS create is idempotent, but do not blindly issue another write:
            # reconciliation belongs to the PMS system of record.
            raise StateError("reservation_outcome_unknown", "PMS pudo haber creado esta reserva. Revísala en PMS antes de cualquier nuevo intento.")
        if not approve:
            raise StateError("human_approval_required", "Confirma desde Telegram con el comando de confirmación. El agente no puede aprobar por ti.")
        record.update(state="creating", payloadHash=draft["approvalPayloadHash"], approval={
            **binding(context), "eventId": context["approvalEventId"], "approvedAt": context["approvedAt"],
        })
        save()  # Claim and approval must be durable before the first PMS write.
        try:
            result = create(record["approval"])
            record.update(state="created", result=result)
            save()
            return result
        except Exception as exc:
            raise StateError("reservation_outcome_unknown", "PMS pudo haber creado esta reserva. Revísala en PMS antes de cualquier nuevo intento.") from exc
