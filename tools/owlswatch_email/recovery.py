"""Durable, bounded Correo intake. This module never invokes a language model."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

OPEN_STATUSES = frozenset({"proposed", "draft_ready", "needs_human", "needs_info", "waiting_for_availability", "waiting_for_payment", "waiting_for_quote", "error"})
CLOSED_STATUSES = frozenset({"resolved", "ignored", "rejected", "superseded"})
STATUS_ALIASES = {"handled": "resolved", "done": "resolved", "sent": "resolved", "closed": "resolved", "completed": "resolved", "archived": "ignored", "no_action_needed": "ignored", "review": "needs_human", "needs_review": "needs_human", "draft_created": "draft_ready"}
BATCH_SIZE = 4
DISCOVERY_PAGE_SIZE = 20


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {} if default is None else default


def root(server):
    return server.WORKSPACE / "tasks" / "email_runtime"


@contextlib.contextmanager
def lock(server, name="state"):
    folder = root(server)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{name}.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def thread_id(task):
    return task.get("gmailThreadId") or task.get("threadId") or (task.get("gmail") or {}).get("threadId") or (task.get("thread") or {}).get("gmailThreadId") or (task.get("thread") or {}).get("threadId")


def canonical_status(status):
    return STATUS_ALIASES.get(status, status)


def compact_task(task):
    names = ("taskId", "gmailThreadId", "gmailThreadUrl", "gmailDraftId", "sourceMessageId", "status", "priority", "owner", "nextActionAt", "lastExternalMessageAt", "resolvedAt", "updatedAt")
    result = {name: task.get(name) for name in names}
    result["status"] = canonical_status(result["status"])
    result["summary"] = str(task.get("summary") or "")[:500]
    return result


def confirmed_staff_reply(server, messages, account):
    meaningful = server.latest_meaningful_message(messages)
    return meaningful if meaningful and "SENT" in meaningful.get("labelIds", []) and server.is_staff_sender(meaningful.get("fromEmail", ""), account) else None


def reconcile_tasks(server, thread, messages, account):
    reply = confirmed_staff_reply(server, messages, account)
    if not reply:
        return 0
    count = 0
    for path in server.TASK_DIR.glob("*.json"):
        task = server.read_task(path)
        if task and thread_id(task) == thread and canonical_status(task.get("status")) in OPEN_STATUSES:
            task.update(status="resolved", resolvedAt=server.now_iso(), resolutionSource="gmail_sent_reply", resolutionMessageId=reply["id"], updatedAt=server.now_iso())
            atomic_json(path, task)
            count += 1
    return count


def _discover(server, service, checkpoint):
    """Persist provider pages before fetching bodies; never discard a partial page."""
    if checkpoint.get("discovery"):
        return checkpoint["discovery"]
    if not checkpoint.get("historyId"):
        bootstrap = checkpoint.get("bootstrap")
        if not bootstrap:
            anchor = service.users().getProfile(userId="me").execute()["historyId"]
            now = server.now_utc()
            start = server.parse_iso_datetime(checkpoint.get("recoveryStartAt")) or now - dt.timedelta(days=7)
            bootstrap = {"anchor": anchor, "query": f"after:{int(start.timestamp())} before:{int(now.timestamp())+1} -in:spam -in:trash", "pageToken": None}
            checkpoint["bootstrap"] = bootstrap
            atomic_json(root(server) / "checkpoint.json", checkpoint)
        kwargs = {"userId": "me", "q": bootstrap["query"], "maxResults": DISCOVERY_PAGE_SIZE}
        if bootstrap.get("pageToken"):
            kwargs["pageToken"] = bootstrap["pageToken"]
        page = service.users().threads().list(**kwargs).execute()
        return {"kind": "bootstrap", "ids": [row["id"] for row in page.get("threads", [])], "nextPageToken": page.get("nextPageToken"), "historyId": bootstrap["anchor"]}
    kwargs = {"userId": "me", "startHistoryId": checkpoint["historyId"], "historyTypes": ["messageAdded"], "maxResults": DISCOVERY_PAGE_SIZE}
    if checkpoint.get("pageToken"):
        kwargs["pageToken"] = checkpoint["pageToken"]
    try:
        page = service.users().history().list(**kwargs).execute()
    except Exception as exc:
        if getattr(getattr(exc, "resp", None), "status", None) == 404:
            # Gmail expired the checkpoint. Retain evidence and bootstrap a bounded recovery.
            last = server.parse_iso_datetime(checkpoint.get("lastAcknowledgedAt")) or server.now_utc() - dt.timedelta(days=30)
            checkpoint.update(expiredHistoryId=checkpoint.pop("historyId"), recoveryRequiredAt=server.now_iso(), recoveryStartAt=(last-dt.timedelta(days=1)).isoformat())
            checkpoint.pop("pageToken", None)
            atomic_json(root(server) / "checkpoint.json", checkpoint)
            return _discover(server, service, checkpoint)
        raise
    ids = sorted({item["message"]["threadId"] for row in page.get("history", []) for item in row.get("messagesAdded", []) if item.get("message", {}).get("threadId")})
    return {"kind": "history", "ids": ids, "nextPageToken": page.get("nextPageToken"), "historyId": page.get("historyId", checkpoint["historyId"])}



def reconcile_open_slice(server, service, checkpoint, account):
    # Rotate a small read-only sweep through old local records. No raw mail enters logs.
    ids = sorted({thread_id(task) for path in server.TASK_DIR.glob("*.json")
                  if (task := server.read_task(path)) and canonical_status(task.get("status")) in OPEN_STATUSES and thread_id(task)})
    if not ids:
        return 0
    offset = int(checkpoint.get("reconcileOffset", 0)) % len(ids)
    batch = (ids + ids)[offset:offset + min(BATCH_SIZE, len(ids))]
    count = 0
    errors = []
    for identifier in batch:
        try:
            messages = server.thread_messages(server.get_thread(service, identifier))
            count += reconcile_tasks(server, identifier, messages, account)
        except Exception:
            errors.append({"threadId": identifier, "code": "reconciliation_read_failed"})
    checkpoint["reconciliationErrors"] = errors
    checkpoint["reconcileOffset"] = (offset + len(batch)) % len(ids)
    atomic_json(root(server) / "checkpoint.json", checkpoint)
    return count


def preflight(server):
    with lock(server):
        active_path = root(server) / "active.json"
        active = read_json(active_path)
        config = server.load_config()
        service = server.google_build_service(config, ["https://www.googleapis.com/auth/gmail.readonly"])
        account = server.gmail_account(config)
        if active and active.get("status") != "completed":
            for candidate in active["candidates"]:
                identifier = candidate["threadId"]
                if identifier in active.get("receipts", {}):
                    continue
                messages = server.thread_messages(server.get_thread(service, identifier))
                active["reconciledCount"] = active.get("reconciledCount", 0) + reconcile_tasks(server, identifier, messages, account)
                latest = server.latest_meaningful_message(messages)
                if not latest or "SENT" in latest.get("labelIds", []) or server.is_low_value_message(latest):
                    active.setdefault("receipts", {})[identifier] = {"messageId": latest["id"] if latest else None, "outcome": "handled_while_pending"}
                elif latest["id"] != candidate["sourceMessageId"]:
                    candidate.update(sourceMessageId=latest["id"], receivedAt=latest.get("date"))
            atomic_json(active_path, active)
            return public_scan(active)
        checkpoint = read_json(root(server) / "checkpoint.json")
        discovery = _discover(server, service, checkpoint)
        checkpoint["discovery"] = discovery
        atomic_json(root(server) / "checkpoint.json", checkpoint)
        ids = discovery["ids"][:BATCH_SIZE]
        processed = read_json(root(server) / "processed.json")
        candidates = []
        receipts = {}
        reconciled = reconcile_open_slice(server, service, checkpoint, account)
        for identifier in ids:
            messages = server.thread_messages(server.get_thread(service, identifier))
            reconciled += reconcile_tasks(server, identifier, messages, account)
            latest = server.latest_meaningful_message(messages)
            if not latest:
                receipts[identifier] = {"messageId": None, "outcome": "empty"}
                continue
            message_id = latest["id"]
            if processed.get(identifier) == message_id or "SENT" in latest.get("labelIds", []) or server.is_low_value_message(latest):
                receipts[identifier] = {"messageId": message_id, "outcome": "unchanged_or_handled"}
                continue
            candidates.append({"threadId": identifier, "sourceMessageId": message_id, "sourceUrl": server.gmail_source_url(identifier), "receivedAt": latest.get("date")})
        active = {"scanId": uuid.uuid4().hex, "status": "awaiting_acknowledgement" if candidates else "ready", "startedAt": server.now_iso(), "candidates": candidates, "receipts": receipts, "threadIds": ids, "reconciledCount": reconciled}
        atomic_json(active_path, active)
        atomic_json(root(server) / "runs" / f"{active['scanId']}.json", active)
        return public_scan(active)


def public_scan(active):
    return {"ok": True, "scanId": active["scanId"], "status": active["status"], "candidates": [c for c in active["candidates"] if c["threadId"] not in active.get("receipts", {})], "reconciledCount": active.get("reconciledCount", 0)}


def acknowledge(server, args):
    with lock(server):
        active = read_json(root(server) / "active.json")
        candidate = next((c for c in active.get("candidates", []) if c["threadId"] == args.get("threadId") and c["sourceMessageId"] == args.get("sourceMessageId")), None)
        if not candidate or active.get("scanId") != args.get("scanId") or active.get("status") == "completed":
            raise server.ToolError("invalid_acknowledgement", "Acknowledgement does not match an active scan item.")
        outcome = args.get("outcome")
        if outcome not in {"ignored", "task_saved"}:
            raise server.ToolError("invalid_input", "outcome must be ignored or task_saved.")
        if outcome == "task_saved":
            task = server.read_task(server.task_path(str(args.get("taskId") or "")))
            if not task or thread_id(task) != candidate["threadId"] or task.get("sourceMessageId") != candidate["sourceMessageId"]:
                raise server.ToolError("task_not_saved", "Save this exact source message as a recovery task before acknowledging it.")
            if canonical_status(task.get("status")) not in OPEN_STATUSES:
                raise server.ToolError("invalid_task_state", "Actionable acknowledgement requires an open recovery task.")
            if task.get("status") == "draft_ready" and not task.get("gmailDraftId"):
                raise server.ToolError("draft_not_confirmed", "A draft-ready task requires a confirmed Gmail draft.")
            notification = server.read_notification(f"gmail-thread-{candidate['threadId']}")
            last_notification = server.parse_iso_datetime((notification or {}).get("lastNotifiedAt"))
            config = server.load_config()
            if (not notification or not notification.get("messageId") or not last_notification
                    or last_notification < server.now_utc() - dt.timedelta(hours=24)
                    or str(notification.get("chatId")) != str(server.email_notify_chat_id(config))
                    or str(notification.get("messageThreadId") or "") != str(server.email_notify_thread_id(config) or "")):
                raise server.ToolError("notification_not_confirmed", "A recent successful handoff to the configured Telegram destination is required before acknowledging actionable work.")
        active.setdefault("receipts", {})[candidate["threadId"]] = {"messageId": candidate["sourceMessageId"], "outcome": outcome, "taskId": args.get("taskId"), "acknowledgedAt": server.now_iso()}
        atomic_json(root(server) / "active.json", active)
        return {"ok": True, "scanId": active["scanId"], "acknowledged": candidate["threadId"]}


def finalize(server, scan_id, process_exit=0):
    with lock(server):
        active = read_json(root(server) / "active.json")
        if active.get("scanId") != scan_id:
            raise server.ToolError("invalid_scan", "The scan no longer matches.")
        if active.get("status") == "completed":
            return {"ok": True, "status": "completed", "scanId": scan_id}
        missing = [c for c in active["candidates"] if c["threadId"] not in active.get("receipts", {})]
        if missing:
            active.update(status="incomplete", lastAttemptAt=server.now_iso(), processExit=process_exit, pendingCount=len(missing))
            atomic_json(root(server) / "active.json", active)
            atomic_json(root(server) / "runs" / f"{scan_id}.json", active)
            return {"ok": False, "status": "incomplete", "pendingCount": len(missing), "scanId": scan_id}
        processed = read_json(root(server) / "processed.json")
        processed.update({key: value["messageId"] for key, value in active["receipts"].items()})
        atomic_json(root(server) / "processed.json", processed)
        checkpoint = read_json(root(server) / "checkpoint.json")
        if checkpoint.get("lastScanId") != scan_id:
            discovery = checkpoint["discovery"]
            discovery["ids"] = discovery["ids"][len(active["threadIds"]):]
            if not discovery["ids"]:
                if discovery["kind"] == "bootstrap" and discovery.get("nextPageToken"):
                    checkpoint["bootstrap"]["pageToken"] = discovery["nextPageToken"]
                elif discovery.get("nextPageToken"):
                    checkpoint["pageToken"] = discovery["nextPageToken"]
                else:
                    checkpoint["historyId"] = discovery["historyId"]
                    checkpoint.pop("pageToken", None)
                    checkpoint.pop("bootstrap", None)
                checkpoint.pop("discovery", None)
            checkpoint["lastAcknowledgedAt"] = server.now_iso()
            checkpoint["lastScanId"] = scan_id
            atomic_json(root(server) / "checkpoint.json", checkpoint)
        active.update(status="completed", completedAt=server.now_iso(), processExit=process_exit)
        atomic_json(root(server) / "active.json", active)
        atomic_json(root(server) / "runs" / f"{scan_id}.json", active)
        return {"ok": True, "status": "completed", "scanId": scan_id, "processedCount": len(active["threadIds"])}
