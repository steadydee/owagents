#!/usr/bin/env python3
"""Installed scheduler entry point. No model is started for unchanged polling."""
from __future__ import annotations
import argparse
import collections
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import recovery
import server


def daily_digest(mode):
    tasks = []
    now = server.now_utc()
    for path in server.TASK_DIR.glob("*.json"):
        task = server.read_task(path)
        if not task or recovery.canonical_status(task.get("status")) not in recovery.OPEN_STATUSES:
            continue
        if mode == "daily_summary":
            received = server.latest_task_message_time(task)
            if not received or received < now - dt.timedelta(days=1):
                continue
        elif server.parse_iso_datetime(task.get("nextActionAt")) and server.parse_iso_datetime(task["nextActionAt"]) > now:
            continue
        tasks.append(task)
    counts = collections.Counter(recovery.canonical_status(task["status"]) for task in tasks)
    heading = "Email review summary (last 24 hours)" if mode == "daily_summary" else "Email review follow-up"
    lines = [heading, "", f"Local review records: {len(tasks)} (Gmail remains the review interface)."]
    lines.extend(f"{name.replace('_', ' ')}: {count}" for name, count in sorted(counts.items()))
    unassigned = sum(not t.get("owner") or t.get("owner") == "unassigned" for t in tasks)
    if unassigned:
        lines.append(f"Unassigned: {unassigned}")
    if mode != "daily_summary":
        lines.append("Older records may need Gmail reconciliation; do not treat this count as confirmed unanswered email.")
    identifiers = list(dict.fromkeys(recovery.thread_id(t) for t in tasks if recovery.thread_id(t)))[:8]
    lines.extend(server.gmail_source_url(identifier) for identifier in identifiers)
    return server.tool_email_send_telegram_message({"text": "\n".join(lines), "dedupeKey": f"{mode}-{now.date()}", "dedupeHours": 24})


def run(mode, force=False, invoke=subprocess.run):
    state = recovery.root(server)
    with recovery.lock(server, "scheduled-run"):
        attempt = {"attemptId": uuid.uuid4().hex, "mode": mode, "startedAt": server.now_iso(), "status": "running"}
        recovery.atomic_json(state / "schedule-status.json", attempt)
        try:
            scan = recovery.preflight(server)
            attempt.update(scanId=scan["scanId"], changedCount=len(scan["candidates"]))
            result_code = 0
            if scan["candidates"]:
                binary = os.environ.get("OPENCLAW_BIN") or shutil.which("openclaw")
                if not binary:
                    raise server.ToolError("runtime_missing", "OpenClaw executable is not configured.")
                payload = {"scanId": scan["scanId"], "candidates": scan["candidates"]}
                prompt = "Scheduled Correo changed-message batch. Gmail is the review interface. Process only these candidates; do not rescan Gmail. Read each thread, create/reuse a draft when safe, persist exact sourceMessageId, notify through the configured tool, then acknowledge each exact item with owlswatch_email_acknowledge_item. Explicitly ignored items also require acknowledgement. Do not claim success from text. Batch: " + json.dumps(payload, separators=(",", ":"))
                response = invoke([binary, "--profile", os.environ.get("OPENCLAW_PROFILE", "owlswatch"), "agent", "--agent", "correo", "--session-id", f"correo-{scan['scanId']}-{attempt['attemptId'][:8]}", "--thinking", "low", "--timeout", "1200", "--message", prompt], timeout=1260, check=False)
                result_code = response.returncode
            result = recovery.finalize(server, scan["scanId"], result_code)
            if not result["ok"]:
                attempt.update(status="incomplete", pendingCount=result["pendingCount"])
                return 1
            if mode != "polling_30m":
                day = server.now_utc().date().isoformat()
                stamp = state / f"{mode}-{day}.json"
                if force or not stamp.exists():
                    notification = daily_digest(mode)
                    if not notification.get("ok"):
                        raise server.ToolError("digest_failed", "Digest delivery was not acknowledged.")
                    recovery.atomic_json(stamp, {"completedAt": server.now_iso(), "notification": notification})
            attempt.update(status="completed" if scan["candidates"] else "unchanged", completedAt=server.now_iso())
            return 0
        except Exception as exc:
            attempt.update(status="failed", error=server.sanitized_error(exc)["error"], failedAt=server.now_iso())
            return 1
        finally:
            recovery.atomic_json(state / "schedule-status.json", attempt)
            recovery.atomic_json(state / "schedule_runs" / f"{attempt['attemptId']}.json", attempt)
            print(json.dumps({key: attempt[key] for key in ("attemptId", "mode", "status", "changedCount", "pendingCount", "error") if key in attempt}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["polling_30m", "daily_summary", "unanswered_7d"])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    enabled = Path(os.environ.get("ENABLED_FILE", "~/.openclaw-owlswatch/email-agent.enabled")).expanduser()
    if not args.force and os.environ.get("OWLSWATCH_EMAIL_AGENT_ENABLED") != "1" and not enabled.exists():
        print(json.dumps({"status": "disabled"}))
        return 0
    if not args.force and args.mode != "polling_30m":
        local = server.now_utc().astimezone()
        if local.hour * 60 + local.minute < (480 if args.mode == "daily_summary" else 495):
            print(json.dumps({"status": "before_schedule"}))
            return 0
    return run(args.mode, args.force)


if __name__ == "__main__":
    sys.exit(main())
