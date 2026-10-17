"""Operator-owned review policy and content-free, evidence-based backlog views.

No Gmail mutation, model call or notification is performed here. Runtime policy
is deliberately separate from the model's task payload.
"""
from __future__ import annotations

import datetime as dt
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
    new_source = bool(existing.get("sourceMessageId") and task.get("sourceMessageId") and existing["sourceMessageId"] != task["sourceMessageId"])
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


def snapshot(server, raw_thread, account):
    messages = server.thread_messages(raw_thread)
    latest = server.latest_meaningful_message(messages)
    external = server.latest_external_message(messages, account)
    reply = recovery.confirmed_staff_reply(server, messages, account)
    return {"threadId": raw_thread.get("id"), "checkedAt": server.now_iso(),
            "latestMessageId": (latest or {}).get("id"),
            "latestExternalMessageId": (external or {}).get("id"),
            "latestExternalMessageAt": (external or {}).get("date"),
            "sentReplyMessageId": (reply or {}).get("id"),
            "messageIds": [message["id"] for message in messages],
            "gmailHasDraft": any("DRAFT" in message.get("labelIds", []) for message in raw_thread.get("messages", [])),
            "state": "sent_reply_verified" if reply else "external_reply_pending" if external and latest and external["id"] == latest["id"] else "no_actionable_source_verified"}


def closure_verified(task, evidence):
    source = task.get("sourceMessageId")
    ids = evidence.get("messageIds", [])
    reply = evidence.get("sentReplyMessageId")
    if not reply or reply not in ids:
        return False
    if source:
        return source in ids and ids.index(source) < ids.index(reply)
    return bool(evidence.get("latestExternalMessageId"))


def evidence_fields(task, evidence):
    state = evidence["state"]
    if task.get("sourceMessageId") and task["sourceMessageId"] not in evidence.get("messageIds", []):
        state = "source_not_found"
    return {"lastReconciledAt": evidence["checkedAt"], "reconciliationState": state,
            "lastVerifiedMessageId": evidence.get("latestMessageId"),
            **({"gmailHasDraft": evidence["gmailHasDraft"]} if "gmailHasDraft" in evidence else {})}


def overview(server, task):
    result = recovery.compact_task(task)
    due = server.parse_iso_datetime(task.get("nextActionAt"))
    result.update(overdue=bool(due and due <= server.now_utc()),
                  overdueHours=round(max(0, (server.now_utc()-due).total_seconds()/3600), 1) if due else None,
                  reconciliationState=task.get("reconciliationState", "unverified"),
                  lastReconciledAt=task.get("lastReconciledAt"), gmailHasDraft=task.get("gmailHasDraft"))
    return result
