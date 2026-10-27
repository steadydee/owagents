"""Operator-owned review policy and content-free, evidence-based backlog views.

No Gmail mutation, model call or notification is performed here. Runtime policy
is deliberately separate from the model's task payload.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import recovery


def policy(server):
    configured = recovery.read_json(recovery.root(server) / "review-policy.json")
    owner = configured.get("defaultOwner") or server.cfg_env(server.load_config(), "OWLSWATCH_EMAIL_REVIEW_OWNER") or "unassigned"
    hours = configured.get("normalSlaHours", 48)
    if not isinstance(owner, str) or not owner.strip() or len(owner) > 120 or "\n" in owner:
        raise server.ToolError("invalid_review_policy", "Review owner must be a short configured label.")
    if not isinstance(hours, int) or not 1 <= hours <= 168:
        raise server.ToolError("invalid_review_policy", "Normal review deadline must be 1–168 hours.")
    return {"defaultOwner": owner, "normalSlaHours": hours}


def metadata(server, task, existing=None, settings=None):
    """Keep human ownership/deadline authority out of model-selected fields."""
    existing = existing or {}
    settings = settings or policy(server)
    owner = existing.get("owner")
    if not owner or owner == "unassigned":
        owner = settings["defaultOwner"]
    new_source = bool(existing and task.get("sourceMessageId") and
                      (existing.get("sourceMessageId") != task["sourceMessageId"]
                       or recovery.thread_id(existing) != recovery.thread_id(task)))
    anchor = (server.now_utc() if new_source else server.latest_task_message_time(existing or task)
              or server.parse_iso_datetime(existing.get("createdAt") or task.get("createdAt"))
              or server.now_utc())
    hours = {"urgent": 2, "high": 8}.get(task.get("priority"), settings["normalSlaHours"])
    due = None if new_source else server.parse_iso_datetime(existing.get("nextActionAt"))
    if not due:
        due = anchor + dt.timedelta(hours=hours)
    return {"owner": owner, "nextActionAt": due.isoformat(),
            "ownerSource": existing.get("ownerSource") or ("configured_review_policy" if owner != "unassigned" else "unassigned"),
            "deadlineSource": existing.get("deadlineSource") or "review_policy"}


def candidate_thread_ids(task, tasks_by_id=None, seen=None):
    """Legacy IDs are candidates only; Gmail must verify them before linkage."""
    direct = recovery.thread_id(task)
    if direct:
        return [direct]
    seen = seen or set()
    task_id = task.get("taskId")
    if task_id in seen:
        return []
    seen.add(task_id)
    raw = str(task.get("id") or "")
    if tasks_by_id and raw in tasks_by_id:
        return candidate_thread_ids(tasks_by_id[raw], tasks_by_id, seen)
    # Historical id fields used literal provider IDs and descriptive prefixes.
    # Never treat email-<hash> task IDs as Gmail IDs.
    if not raw.startswith("email-"):
        match = re.fullmatch(r"(?:.*[-_])?([a-f0-9]{16})", raw)
        if match:
            return [match[1]]
    for url in (task.get("sourceUrl"), task.get("gmailThreadUrl"), (task.get("gmail") or {}).get("sourceUrl")):
        match = server_url_id(url)
        if match:
            return [match]
    return []


def server_url_id(url):
    match = re.fullmatch(r"https://mail\.google\.com/mail/u/\d+/#\S+/([a-f0-9]{16})", str(url or ""))
    return match[1] if match else None


EVIDENCE_MAX_AGE_HOURS = 24
EVIDENCE_FIELDS = frozenset({"lastReconciledAt", "reconciliationState", "lastVerifiedMessageId",
    "lastVerifiedSentMessageId", "gmailHasDraft", "lastVerifiedExternalMessageAt", "lastVerifiedSourceSubject",
    "lastVerifiedDraftId", "lastVerifiedDraftSourceMessageId"})


def _account_hash(account):
    return hashlib.sha256(account.lower().strip().encode()).hexdigest()


def _evidence_path(server, thread, account):
    key = hashlib.sha256(f"{account.lower().strip()}:{thread}".encode()).hexdigest()
    return recovery.root(server) / "thread-evidence" / f"{key}.json"


def _confirmed_operation(server, thread, source, account):
    key = hashlib.sha256(f"{account}:{thread}:{source}".encode()).hexdigest()
    path = recovery.root(server) / "draft_operations" / f"{key}.json"
    try:
        if path.is_symlink() or path.stat().st_size > 65536:
            return None
        record = recovery.read_json(path)
    except (OSError, ValueError):
        return None
    if (not isinstance(record, dict) or record.get("status") != "confirmed" or record.get("operationId") != key
            or record.get("threadId") != thread or record.get("sourceMessageId") != source
            or record.get("messageId") != f"<ow-correo-{key}@owlswatch.com>"
            or not isinstance(record.get("gmailDraftId"), str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,180}", record["gmailDraftId"])):
        return None
    return record


def remember_thread(server, raw_thread, account):
    """Retain provider metadata from an existing read; never fetch another thread."""
    evidence = snapshot(server, raw_thread, account)
    thread = evidence.get("threadId")
    if not isinstance(thread, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,180}", thread):
        return evidence
    path = _evidence_path(server, thread, account)
    with recovery.lock(server, "thread-evidence-" + path.stem):
        recovery.atomic_json(path, evidence)
    return evidence


def recent_evidence(server, thread, account):
    """Only the private tool-owned, account-bound snapshot is authority."""
    if not isinstance(thread, str) or not thread:
        return None
    path = _evidence_path(server, thread, account)
    try:
        if path.is_symlink() or path.stat().st_size > 262144:
            return None
        value = recovery.read_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    checked = server.parse_iso_datetime(value.get("checkedAt"))
    # Require explicit timezone information; the general legacy date reader is
    # intentionally permissive and is not suitable for an authority timestamp.
    try:
        aware = dt.datetime.fromisoformat(str(value.get("checkedAt")).replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        aware = False
    if (value.get("schemaVersion") != 1 or value.get("threadId") != thread
            or value.get("accountHash") != _account_hash(account) or not checked or not aware
            or not server.now_utc() - dt.timedelta(hours=EVIDENCE_MAX_AGE_HOURS) <= checked <= server.now_utc()
            or not isinstance(value.get("state"), str)
            or value["state"] not in {"sent_reply_verified", "external_reply_pending", "no_actionable_source_verified"}
            or not isinstance(value.get("messageIds"), list)
            or not all(isinstance(item, str) for item in value["messageIds"])):
        return None
    for key in ("latestMessageId", "latestExternalMessageId", "sentReplyMessageId"):
        if value.get(key) is not None and (not isinstance(value[key], str) or value[key] not in value["messageIds"]):
            return None
    external_at = value.get("latestExternalMessageAt")
    if external_at is not None:
        try:
            parsed = dt.datetime.fromisoformat(external_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return None
        if parsed.tzinfo is None or parsed > checked:
            return None
    return value


def apply_recent_evidence(server, task, account):
    evidence = recent_evidence(server, recovery.thread_id(task), account)
    if evidence is None:
        return
    # The source is selected by the caller but only provider-observed membership
    # and latest IDs can make it eligible. A mismatch never inherits old proof.
    task.update(evidence_fields(task, evidence))


def remember_created_draft(server, result, account):
    """A new positive create receipt is useful immediately; reused journals are not."""
    if result.get("ok") is not True or result.get("reused") is not False:
        return
    thread, source = result.get("threadId"), result.get("sourceMessageId")
    path = _evidence_path(server, thread, account)
    with recovery.lock(server, "thread-evidence-" + path.stem):
        evidence = recent_evidence(server, thread, account)
        operation = _confirmed_operation(server, thread, source, account)
        if (not evidence or not operation or operation["gmailDraftId"] != result.get("gmailDraftId")
                or evidence.get("state") != "external_reply_pending"
                or evidence.get("latestMessageId") != source or evidence.get("latestExternalMessageId") != source):
            return
        # Preserve the original provider-read time. Creation does not refresh
        # verification of the request, subject or external message date.
        evidence.update(gmailHasDraft=True, confirmedDraftId=operation["gmailDraftId"],
                        confirmedDraftSourceMessageId=source, draftObservedAt=server.now_iso(),
                        draftEvidenceSource="create_receipt")
        recovery.atomic_json(path, evidence)


def snapshot(server, raw_thread, account):
    messages = server.thread_messages(raw_thread)
    latest = server.latest_meaningful_message(messages)
    external = server.latest_external_message(messages, account)
    reply = recovery.confirmed_staff_reply(server, messages, account)
    drafts = [message for message in raw_thread.get("messages", []) if "DRAFT" in message.get("labelIds", [])]
    # Gmail threads expose draft *message* IDs, not draft resource IDs. Resolve
    # an exact resource ID only through our confirmed operation and its unique
    # RFC Message-ID observed in the current provider DRAFT message.
    operation = _confirmed_operation(server, raw_thread.get("id"), (external or {}).get("id"), account)
    exact_draft = operation if operation and any(server.headers_map(message).get("message-id") == operation["messageId"] for message in drafts) else None
    return {"schemaVersion": 1, "accountHash": _account_hash(account),
            "threadId": raw_thread.get("id"), "checkedAt": server.now_iso(),
            "latestMessageId": (latest or {}).get("id"),
            "latestExternalMessageId": (external or {}).get("id"),
            "latestExternalMessageAt": (external or {}).get("date"),
            "latestExternalSubject": str((external or {}).get("subject") or "")[:300],
            "sentReplyMessageId": (reply or {}).get("id"),
            "messageIds": [message["id"] for message in messages],
            "gmailHasDraft": bool(drafts),
            "draftMessageIds": [message["id"] for message in drafts if isinstance(message.get("id"), str)],
            "confirmedDraftId": exact_draft["gmailDraftId"] if exact_draft else None,
            "confirmedDraftSourceMessageId": exact_draft["sourceMessageId"] if exact_draft else None,
            "draftEvidenceSource": "provider_read",
            "state": "sent_reply_verified" if reply else "external_reply_pending" if external and latest and external["id"] == latest["id"] else "no_actionable_source_verified"}


def closure_verified(task, evidence):
    # A sent email does not prove payment, availability, quote work, or another
    # business dependency completed. Those handoffs remain explicitly open.
    if recovery.canonical_status(task.get("status")) not in {"proposed", "draft_ready", "needs_human", "error"}:
        return False
    source = task.get("sourceMessageId")
    ids = evidence.get("messageIds", [])
    reply = evidence.get("sentReplyMessageId")
    if not reply or reply not in ids:
        return False
    return bool(source and source in ids and ids.index(source) < ids.index(reply))


def evidence_fields(task, evidence):
    state = evidence["state"]
    source = task.get("sourceMessageId")
    if source and source not in evidence.get("messageIds", []):
        state = "source_not_found"
    elif evidence.get("sentReplyMessageId") and not source:
        state = "source_message_unverified"
    elif evidence.get("sentReplyMessageId") and recovery.canonical_status(task.get("status")) in {"needs_info", "waiting_for_payment", "waiting_for_availability", "waiting_for_quote"}:
        state = "business_outcome_unverified"
    exact = bool(source and source == evidence.get("latestMessageId") == evidence.get("latestExternalMessageId")
                 and state == "external_reply_pending")
    draft_id = evidence.get("confirmedDraftId") if exact and source == evidence.get("confirmedDraftSourceMessageId") else None
    return {"lastReconciledAt": evidence["checkedAt"], "reconciliationState": state,
            "lastVerifiedMessageId": evidence.get("latestMessageId"),
            "lastVerifiedSentMessageId": evidence.get("sentReplyMessageId"),
            "lastVerifiedExternalMessageAt": evidence.get("latestExternalMessageAt") if exact else None,
            "lastVerifiedSourceSubject": evidence.get("latestExternalSubject") if exact else None,
            "lastVerifiedDraftId": draft_id, "lastVerifiedDraftSourceMessageId": source if draft_id else None,
            **({"gmailHasDraft": evidence["gmailHasDraft"]} if "gmailHasDraft" in evidence else {})}


def overview(server, task):
    result = recovery.compact_task(task)
    due = server.parse_iso_datetime(task.get("nextActionAt"))
    result.update(overdue=bool(due and due <= server.now_utc()),
                  overdueHours=round(max(0, (server.now_utc()-due).total_seconds()/3600), 1) if due else None,
                  reconciliationState=task.get("reconciliationState", "unverified"),
                  lastReconciledAt=task.get("lastReconciledAt"), gmailHasDraft=task.get("gmailHasDraft"))
    return result
