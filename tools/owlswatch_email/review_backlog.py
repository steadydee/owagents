#!/usr/bin/env python3
"""Bounded operator reconciliation. Gmail reads only; no model or notifications."""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import hashlib
import json
from pathlib import Path
import handoffs
import recovery
import server


def fingerprint(task):
    return hashlib.sha256(json.dumps(task, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def review(state_dir, *, read_gmail=False, limit=30, apply=False, owner=None, sla_hours=48):
    if not 1 <= limit <= 200:
        raise ValueError("limit must be 1–200")
    evidence_path = state_dir / "gmail-evidence.json"
    evidence = recovery.read_json(evidence_path)
    previous = recovery.read_json(state_dir / "inventory.json")
    policy_args = {"owner": owner, "slaHours": sla_hours}
    if owner is not None and (not owner.strip() or len(owner) > 120 or "\n" in owner or not 1 <= sla_hours <= 168):
        raise ValueError("Invalid review policy")
    if apply and (not previous.get("taskFingerprints") or previous.get("policy") != policy_args):
        raise ValueError("Run and inspect a plan with the same owner/deadline policy before applying")
    all_tasks = [task for path in server.TASK_DIR.glob("*.json") if (task := server.read_task(path))]
    index = {task["taskId"]: task for task in all_tasks if task.get("taskId")}
    fingerprints = {key: fingerprint(task) for key, task in index.items()}
    tasks = [task for task in all_tasks if recovery.canonical_status(task.get("status")) in recovery.OPEN_STATUSES]
    candidates = {task["taskId"]: handoffs.candidate_thread_ids(task, index) for task in tasks}
    identifiers = sorted({value for values in candidates.values() for value in values})
    stale = server.now_utc() - dt.timedelta(hours=24)
    pending = [key for key in identifiers if (server.parse_iso_datetime(evidence.get(key, {}).get("checkedAt")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) < stale]
    # Refresh provider evidence immediately before every potential closure.
    # A cached SENT reply alone must not hide new inbound mail since the audit.
    if apply:
        pending = [key for key in identifiers if evidence.get(key, {}).get("sentReplyMessageId")]
        if len(pending) > limit:
            raise ValueError("Raise the bounded limit to cover all closure rechecks before applying")
    reads = 0
    refreshed = set()
    if (read_gmail or apply) and pending:
        config = server.load_config()
        service = server.google_build_service(config, ["https://www.googleapis.com/auth/gmail.readonly"])
        for identifier in pending[:limit]:
            try:
                raw = server.get_thread(service, identifier)
                if raw.get("id") != identifier:
                    raise ValueError("Provider thread identity mismatch")
                evidence[identifier] = handoffs.snapshot(server, raw, server.gmail_account(config))
                refreshed.add(identifier)
            except Exception as exc:
                status = getattr(getattr(exc, "resp", None), "status", None)
                evidence[identifier] = {"checkedAt": server.now_iso(), "state": "read_failed", "code": "not_found" if status == 404 else "provider_read_failed", "httpStatus": status}
            reads += 1
            recovery.atomic_json(evidence_path, evidence)
    changes = []
    writes = []
    skipped = []
    with recovery.lock(server):
        for old in tasks:
            task = server.read_task(server.task_path(old["taskId"]))
            if not task or (apply and previous["taskFingerprints"].get(old["taskId"]) != fingerprint(task)):
                skipped.append(old["taskId"])
                continue
            if recovery.canonical_status(task.get("status")) not in recovery.OPEN_STATUSES:
                skipped.append(old["taskId"])
                continue
            before = dict(task)
            task.update(handoffs.metadata(server, task, task, {"defaultOwner": owner.strip(), "normalSlaHours": sla_hours} if owner is not None else None))
            ids = candidates[task["taskId"]]
            verified = next((evidence[key] for key in ids if evidence.get(key, {}).get("threadId") == key and (server.parse_iso_datetime(evidence[key].get("checkedAt")) or stale) > stale), None)
            if verified:
                task.update(gmailThreadId=verified["threadId"], **handoffs.evidence_fields(task, verified))
                if handoffs.closure_verified(task, verified) and (not apply or verified["threadId"] in refreshed):
                    task.update(status="resolved", resolvedAt=server.now_iso(), resolutionSource="gmail_sent_reply", resolutionMessageId=verified["sentReplyMessageId"])
            elif not ids:
                task["reconciliationState"] = "source_identity_missing"
            else:
                task["reconciliationState"] = "unverified"
            if task != before:
                changes.append({"taskId": task["taskId"], "before": {key: before.get(key) for key in task if before.get(key) != task[key]}, "after": {key: value for key, value in task.items() if before.get(key) != value}})
                if apply:
                    task["updatedAt"] = server.now_iso()
                    writes.append((before, task))
            old.update(task)
        if apply:
            # Persist full rollback state before the first local task/policy write.
            stamp = server.now_utc().strftime('%Y%m%dT%H%M%S%fZ')
            recovery.atomic_json(state_dir / f"backup-{stamp}.json", {"policyBefore": recovery.read_json(recovery.root(server)/"review-policy.json"), "tasks": [before for before, _ in writes], "changes": changes, "skippedChangedTaskIds": skipped})
            if owner is not None:
                recovery.atomic_json(recovery.root(server) / "review-policy.json", {"defaultOwner": owner.strip(), "normalSlaHours": sla_hours, "configuredAt": server.now_iso(), "source": "operator"})
            for _, task in writes:
                recovery.atomic_json(server.task_path(task["taskId"]), task)
            recovery.atomic_json(state_dir / f"changes-{stamp}.json", changes)
    open_tasks = [task for task in tasks if recovery.canonical_status(task["status"]) in recovery.OPEN_STATUSES]
    current = [key for key in identifiers if (server.parse_iso_datetime(evidence.get(key, {}).get("checkedAt")) or stale) > stale]
    summary = {"mode": "applied" if apply else "plan", "generatedAt": server.now_iso(), "localActiveRecordsBefore": len(tasks),
               "localActiveRecordsAfter": len(open_tasks), "candidateThreads": len(identifiers), "gmailReadsThisRun": reads,
               "freshThreadChecks": len(current), "remainingThreadChecks": len(identifiers)-len(current),
               "skippedChangedTasks": len(skipped),
               "verifiedSentClosures": len(tasks)-len(open_tasks), "unassigned": sum(task.get("owner") == "unassigned" for task in open_tasks),
               "overdue": sum(handoffs.overview(server, task)["overdue"] for task in open_tasks),
               "reconciliationStates": dict(collections.Counter(task.get("reconciliationState", "unverified") for task in open_tasks)),
               "providerStates": dict(collections.Counter(evidence[key].get("state") for key in current)),
               "note": "Local recovery records are not counts of unanswered emails. No Gmail mutation or notification was performed."}
    recovery.atomic_json(state_dir / "inventory.json", {"summary": summary, "policy": policy_args, "taskFingerprints": fingerprints, "tasks": [handoffs.overview(server, task) for task in open_tasks]})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=recovery.root(server)/"handoff-review")
    parser.add_argument("--read-gmail", action="store_true")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--owner")
    parser.add_argument("--sla-hours", type=int, default=48)
    args = parser.parse_args()
    print(json.dumps(review(args.state_dir, read_gmail=args.read_gmail, limit=args.limit, apply=args.apply, owner=args.owner, sla_hours=args.sla_hours), indent=2))


if __name__ == "__main__":
    main()
