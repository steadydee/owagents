"""Ensure one draft per source message. Ambiguous provider outcomes fail closed."""
from __future__ import annotations
import hashlib
from email.message import EmailMessage
import recovery


def ensure(server, args):
    config = server.load_config()
    if not server.gmail_drafts_enabled(config):
        raise server.ToolError("gmail_drafts_disabled", "Gmail draft creation is disabled.")
    thread = server.validate_safe_id("threadId", args.get("threadId"))
    service = server.google_build_service(config, ["https://www.googleapis.com/auth/gmail.compose"])
    raw_thread = server.get_thread(service, thread)
    messages = server.thread_messages(raw_thread)
    source = server.latest_external_message(messages, server.gmail_account(config))
    latest = server.latest_meaningful_message(messages)
    if not source or not latest or latest["id"] != source["id"]:
        raise server.ToolError("thread_already_handled", "The latest meaningful message is not an external request. Refresh the thread.")
    recipient = server.parsed_email_address(source.get("replyTo") or source.get("fromEmail", ""))
    requested = server.parsed_email_address(args.get("to", ""))
    if not recipient or requested != recipient or not server.EMAIL_RE.fullmatch(args.get("to", "")):
        raise server.ToolError("recipient_mismatch", "Draft recipient must match the current external message reply address.")
    subject = server.validate_text("subject", args.get("subject"), required=True, max_len=300)
    body = server.validate_text("body", args.get("body"), required=True, max_len=50000)
    key = hashlib.sha256(f"{server.gmail_account(config)}:{thread}:{source['id']}".encode()).hexdigest()
    message_id = f"<ow-correo-{key}@owlswatch.com>"
    path = recovery.root(server) / "draft_operations" / f"{key}.json"
    with recovery.lock(server, f"draft-{key}"):
        record = recovery.read_json(path)
        if record.get("status") == "confirmed":
            return {"ok": True, "gmailDraftId": record["gmailDraftId"], "threadId": thread, "sourceMessageId": source["id"], "reused": True}
        if record:
            page = service.users().drafts().list(userId="me", q=f"rfc822msgid:{message_id}", maxResults=10).execute()
            for row in page.get("drafts", []):
                draft = service.users().drafts().get(userId="me", id=row["id"], format="metadata").execute()
                message = draft.get("message") or {}
                if message.get("threadId") == thread and server.headers_map(message).get("message-id") == message_id:
                    record.update(status="confirmed", gmailDraftId=row["id"], reconciledAt=server.now_iso())
                    recovery.atomic_json(path, record)
                    return {"ok": True, "gmailDraftId": row["id"], "threadId": thread, "sourceMessageId": source["id"], "reused": True}
            record.update(status="unknown", reconciledAt=server.now_iso())
            recovery.atomic_json(path, record)
            raise server.ToolError("draft_outcome_unknown", "A previous Gmail create may have succeeded. No confirmed draft was found; human reconciliation is required before creating another.")
        if any("DRAFT" in message.get("labelIds", []) for message in raw_thread.get("messages", [])):
            raise server.ToolError("existing_gmail_draft", "This thread already contains a Gmail draft. Preserve it for human review instead of creating another.")
        record = {"operationId": key, "threadId": thread, "sourceMessageId": source["id"], "messageId": message_id, "status": "creating", "startedAt": server.now_iso(), "contentHash": hashlib.sha256(f"{recipient}\n{subject}\n{body}".encode()).hexdigest()}
        recovery.atomic_json(path, record)
        message = EmailMessage()
        message["To"] = recipient
        message["From"] = server.gmail_account(config)
        message["Subject"] = subject
        message["Message-ID"] = message_id
        if source.get("messageIdHeader"):
            message["In-Reply-To"] = source["messageIdHeader"]
            message["References"] = source["messageIdHeader"]
        message.set_content(body)
        try:
            draft = service.users().drafts().create(userId="me", body={"message": {"raw": server.b64url(message.as_bytes()), "threadId": thread}}).execute()
            if not draft.get("id"):
                raise ValueError("missing draft id")
        except Exception as exc:
            record.update(status="unknown", failedAt=server.now_iso())
            recovery.atomic_json(path, record)
            raise server.ToolError("draft_outcome_unknown", "Gmail draft creation has an unknown outcome. Retry only to reconcile the existing operation; never create a replacement blindly.") from exc
        record.update(status="confirmed", gmailDraftId=draft["id"], confirmedAt=server.now_iso())
        recovery.atomic_json(path, record)
        return {"ok": True, "gmailDraftId": draft["id"], "threadId": thread, "sourceMessageId": source["id"], "reused": False}
