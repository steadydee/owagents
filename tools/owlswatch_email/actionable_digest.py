"""Local-only selection and durable deduplication for useful Correo digests."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import unicodedata

import recovery

MAX_ITEMS = 3
EVIDENCE_AGE = dt.timedelta(hours=24)
SOURCE_AGE = dt.timedelta(days=7)
STATUSES = {"needs_human": 0, "needs_info": 1, "draft_ready": 2,
            "waiting_for_availability": 3, "waiting_for_quote": 4,
            "waiting_for_payment": 5, "proposed": 6, "error": 7}
JOURNAL_STATES = {"sending", "delivered", "outcome_unknown", "failed_not_sent"}


def clean_text(value, limit):
    if not isinstance(value, str):
        return ""
    value = re.sub(r"https?://\S+", "", value)
    value = "".join(c if not unicodedata.category(c).startswith("C") else " " for c in value)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def recent(server, value, now, age):
    at = server.parse_iso_datetime(value)
    return at if at and dt.timedelta(0) <= now - at <= age else None


def eligible(server, task, state, config, now):
    status = recovery.canonical_status(task.get("status"))
    thread, source = recovery.thread_id(task), task.get("sourceMessageId")
    if (status not in STATUSES or not isinstance(thread, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", thread)
            or not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", source)
            or task.get("reconciliationState") != "external_reply_pending"
            or task.get("lastVerifiedMessageId") != source
            or not recent(server, task.get("lastReconciledAt"), now, EVIDENCE_AGE)):
        return None
    received = recent(server, task.get("lastVerifiedExternalMessageAt"), now, SOURCE_AGE)
    if not received:
        return None
    subject = clean_text(task.get("lastVerifiedSourceSubject"), 120)
    summary = clean_text(task.get("summary"), 240)
    action = clean_text(task.get("nextAction"), 180)
    if (len(subject) < 4 or subject.casefold() in {"no subject", "(no subject)", "email", "correo"}
            or len(summary) < 20 or summary.casefold() == subject.casefold()):
        return None
    draft = bool(status == "draft_ready" and task.get("gmailDraftId")
                 and task.get("lastVerifiedDraftId") == task["gmailDraftId"]
                 and task.get("lastVerifiedDraftSourceMessageId") == source)
    # A historical draft_ready label is not proof that a usable draft exists.
    if status == "draft_ready" and not draft:
        return None
    if draft and not action:
        action = "Review the confirmed draft in Gmail, edit if needed, then send."
    if len(action) < 12 or action.casefold() in {"review email", "review in gmail", "follow up", "needs review"}:
        return None
    # Free-text summaries/actions must not turn historical claims into draft proof.
    if not draft and re.search(r"\b(?:draft|borrador)\b", summary + " " + action, re.I):
        return None
    identity = {"threadId": thread, "sourceMessageId": source, "status": status,
                "subject": subject, "summary": summary, "nextAction": action, "draftConfirmed": draft}
    # Presentation edits must not repeat the same business handoff. A changed
    # source, status or concrete action is a new item; a new check time is not.
    business = {key: identity[key] for key in ("threadId", "sourceMessageId", "status", "draftConfirmed")}
    business["nextAction"] = " ".join(re.findall(r"\w+", action.casefold()))
    fingerprint = hashlib.sha256(json.dumps(business, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return {**identity, "fingerprint": fingerprint, "checkedAt": task["lastReconciledAt"],
            "receivedAt": received.isoformat(), "updatedAt": task.get("updatedAt"),
            "priority": {"urgent": 0, "high": 1, "normal": 2, "low": 3}.get(task.get("priority"), 2)}


def read_journal(state):
    path = state / "digest-items.json"
    value = recovery.read_json(path, {"version": 1, "items": {}})
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("items"), dict):
        raise ValueError("invalid digest item journal")
    for key, row in value["items"].items():
        if (not re.fullmatch(r"[a-f0-9]{64}", key) or not isinstance(row, dict)
                or row.get("status") not in JOURNAL_STATES or row.get("mode") not in {"daily_summary", "unanswered_7d"}
                or not isinstance(row.get("day"), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["day"])
                or any(not isinstance(row.get(field), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", row[field])
                       for field in ("threadId", "sourceMessageId"))):
            raise ValueError("invalid digest item journal row")
    return value


def transition(state, journal, items, status, mode, day, at):
    for item in items:
        journal["items"][item["fingerprint"]] = {"status": status, "mode": mode, "day": day, "updatedAt": at,
                                               "threadId": item["threadId"], "sourceMessageId": item["sourceMessageId"]}
    recovery.atomic_json(state / "digest-items.json", journal)


def selection(items):
    # Receipt/attempt metadata retains source identities and fingerprints, not content.
    return [{key: item[key] for key in ("fingerprint", "threadId", "sourceMessageId", "status", "checkedAt")} for item in items]


def immediate_receipt(server, item, config, now):
    record = server.read_notification(f"gmail-thread-{item['threadId']}")
    at = recent(server, (record or {}).get("lastNotifiedAt"), now, EVIDENCE_AGE)
    # The send receipt cannot prove which version of task wording it covered;
    # generic updatedAt advances on identical upserts. Prefer 24 hours of quiet
    # for the same source. A genuinely newer provider message remains eligible.
    received = server.parse_iso_datetime(item["receivedAt"])
    return bool(record and type(record.get("messageId")) is int and record["messageId"] > 0 and at and at >= received
                and str(record.get("chatId")) == str(server.email_notify_chat_id(config))
                and str(record.get("messageThreadId") or "") == str(server.email_notify_thread_id(config) or ""))


def choose(server, state, config, now, journal):
    tasks = [task for path in sorted(server.TASK_DIR.glob("*.json")) if isinstance(task := server.read_task(path), dict)]
    candidates = []
    for task in tasks:
        item = eligible(server, task, state, config, now)
        if not item:
            continue
        checked = server.parse_iso_datetime(item["checkedAt"])
        # A newer saved read of the same conversation overrides stale task copies.
        contradicted = any(recovery.thread_id(other) == item["threadId"]
            and (other_at := server.parse_iso_datetime(other.get("lastReconciledAt"))) and other_at >= checked
            and (other.get("lastVerifiedMessageId") != item["sourceMessageId"]
                 or other.get("reconciliationState") != "external_reply_pending"
                 or recovery.canonical_status(other.get("status")) in recovery.CLOSED_STATUSES)
            for other in tasks)
        if contradicted:
            continue
        candidates.append(item)
    # Newest useful record wins per conversation. Distinct source/status/action
    # changes stay eligible, while check timestamps never change the fingerprint.
    candidates.sort(key=lambda item: (server.parse_iso_datetime(item["checkedAt"]).timestamp(),
                    (server.parse_iso_datetime(item.get("updatedAt")) or server.parse_iso_datetime(item["receivedAt"])).timestamp()), reverse=True)
    unique = {}
    for item in candidates:
        unique.setdefault(item["threadId"], item)
    pending = [item for item in unique.values()
               if journal["items"].get(item["fingerprint"], {}).get("status") not in {"sending", "outcome_unknown", "delivered"}
               and not any(row["threadId"] == item["threadId"] and row["sourceMessageId"] == item["sourceMessageId"]
                           and row["status"] in {"sending", "outcome_unknown"} for row in journal["items"].values())
               and not immediate_receipt(server, item, config, now)]
    pending.sort(key=lambda item: (item["priority"], STATUSES[item["status"]], -server.parse_iso_datetime(item["receivedAt"]).timestamp(), item["threadId"]))
    return pending[:MAX_ITEMS]


def render(server, items):
    lines = ["Email needing attention"]
    for index, item in enumerate(items, 1):
        lines.extend(["", f"{index}. {item['subject']}", item["summary"], f"Next: {item['nextAction']}",
                      f"Open in Gmail: {server.gmail_source_url(item['threadId'])}"])
    return "\n".join(lines)
