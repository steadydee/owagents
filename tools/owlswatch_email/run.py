#!/usr/bin/env python3
"""Installed scheduler entry point. No model is started for unchanged polling."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import urllib.error
from zoneinfo import ZoneInfo
import recovery
import server
import actionable_digest as digest


DIGEST_MINUTES = {"daily_summary": 480, "unanswered_7d": 495}
DIGEST_MAX_ATTEMPTS = 4
DIGEST_RETRY_SECONDS = 1800


def schedule_day(now):
    return now.astimezone(ZoneInfo("America/Bogota")).date().isoformat()


def digest_already_run(mode, state, now):
    day = schedule_day(now)
    stamp = state / f"{mode}-{day}.json"
    if stamp.exists():
        record = recovery.read_json(stamp)
        if record.get("status") != "quiet":
            return True
        completed = server.parse_iso_datetime(record.get("completedAt"))
        if (record.get("mode") != mode or record.get("day") != day
                or record.get("reason") != "no_new_actionable_items" or type(record.get("itemCount")) is not int
                or record["itemCount"] != 0 or "notification" in record
                or not completed or completed > now or schedule_day(completed) != day):
            raise ValueError("invalid quiet digest stamp")
    # Honor an already-run legacy schedule during upgrade without asserting
    # that its old model response proved delivery. Never resend just to migrate.
    old_name = {"daily_summary": "correo-daily-summary", "unanswered_7d": "correo-unanswered"}.get(mode)
    legacy = Path(os.environ.get("OWLSWATCH_LEGACY_STAMP_DIR", "~/.openclaw-owlswatch/schedule-stamps")).expanduser()
    return bool(old_name and (legacy / f"{old_name}-{day}.stamp").exists())


def safe_error(exc):
    value = server.sanitized_error(exc)["error"]
    return {"code": value["code"], "retryable": value.get("retryable") is True}


def confirmed_digest_receipt(mode, now, config):
    """Reconcile a lost scheduler response from the send tool's durable receipt."""
    record = server.read_notification(f"custom-{mode}-{schedule_day(now)}")
    at = server.parse_iso_datetime((record or {}).get("lastNotifiedAt"))
    if (record and type(record.get("messageId")) is int and record["messageId"] > 0
            and at and schedule_day(at) == schedule_day(now)
            and str(record.get("chatId")) == str(server.email_notify_chat_id(config))
            and str(record.get("messageThreadId") or "") == str(server.email_notify_thread_id(config) or "")):
        return {"ok": True, "message_id": record["messageId"],
                "message_thread_id": record.get("messageThreadId"), "suppressed": True}
    return None


def known_not_sent(exc):
    # A timeout/connection loss can follow a successful send. Never infer that
    # the notification was not delivered merely because its response was lost.
    if isinstance(exc, server.ToolError):
        if exc.code in {"config_missing", "config_invalid", "invalid_input", "destination_override_denied", "telegram_error"}:
            return True
        cause = exc.__cause__
        return exc.code == "http_error" and isinstance(cause, urllib.error.HTTPError) and 400 <= cause.code < 500
    return False


def deliver_digest(mode, state, now):
    day = schedule_day(now)
    stamp = state / f"{mode}-{day}.json"
    if digest_already_run(mode, state, now):
        return {"status": "already_recorded", "attempted": False}
    attempt_path = state / "digest-attempts" / f"{mode}-{day}.json"
    previous = recovery.read_json(attempt_path)
    config = server.load_config()
    journal = digest.read_journal(state)
    receipt = confirmed_digest_receipt(mode, now, config)
    if receipt:
        selected = previous.get("selection", [])
        digest.transition(state, journal, selected, "delivered", mode, day, server.now_iso())
        recovery.atomic_json(stamp, {"completedAt": server.now_iso(), "notification": receipt, "selection": selected})
        return {"status": "delivered", "reconciled": True, "attempted": False}
    if previous.get("status") in {"sending", "outcome_unknown", "delivered"}:
        return {"status": "outcome_unknown", "attempts": previous.get("attempts", 0), "attempted": False}
    if any((row := journal["items"].get(item["fingerprint"], {})).get("status") in {"sending", "outcome_unknown", "delivered"}
           and row.get("mode") == mode and row.get("day") == day for item in previous.get("selection", [])):
        # The shared item reservation is durable before the per-mode send claim.
        # A crash between these writes must remain visible, never become quiet.
        previous.update(status="outcome_unknown", failedAt=server.now_iso(),
                        error={"code": "digest_attempt_interrupted", "retryable": False})
        recovery.atomic_json(attempt_path, previous)
        return {"status": "outcome_unknown", "attempts": previous.get("attempts", 0), "attempted": False}
    attempts = int(previous.get("attempts", 0))
    if attempts >= DIGEST_MAX_ATTEMPTS:
        return {"status": "attempt_limit", "attempts": attempts, "attempted": False}
    next_retry = server.parse_iso_datetime(previous.get("nextRetryAt"))
    if next_retry and now < next_retry:
        return {"status": "retry_wait", "nextRetryAt": next_retry.isoformat(), "attempts": attempts, "attempted": False}
    items = digest.choose(server, state, config, now, journal)
    if not items:
        recovery.atomic_json(stamp, {"mode": mode, "day": day, "status": "quiet",
            "reason": "no_new_actionable_items", "completedAt": server.now_iso(), "itemCount": 0})
        return {"status": "quiet", "reason": "no_new_actionable_items", "attempted": False}
    selected = digest.selection(items)
    attempt = {"mode": mode, "day": day, "attempts": attempts + 1, "startedAt": server.now_iso(),
               "status": "preparing", "selection": selected}
    # Persist before preparation and before transport. Crashes during the send
    # remain uncertain and cannot produce a duplicate on the next half-hour run.
    recovery.atomic_json(attempt_path, attempt)
    try:
        text = digest.render(server, items)
        server.email_notify_chat_id(config)
        server.telegram_token(config)
        # Reserve business fingerprints across both modes/days before transport.
        # A crash after this point cannot silently reset an uncertain item.
        attempt["status"] = "sending"
        digest.transition(state, journal, selected, "sending", mode, day, server.now_iso())
        recovery.atomic_json(attempt_path, attempt)
        notification = server.tool_email_send_telegram_message({"text": text, "dedupeKey": f"{mode}-{day}", "dedupeHours": 24})
        receipt = confirmed_digest_receipt(mode, now, config)
        if notification.get("suppressed"):
            notification = receipt
        if (not notification or notification.get("ok") is not True
                or type(notification.get("message_id")) is not int or notification["message_id"] <= 0
                or not receipt or receipt["message_id"] != notification["message_id"]):
            raise server.ToolError("digest_outcome_unknown", "Digest delivery lacks a confirmed message receipt.")
        digest.transition(state, journal, selected, "delivered", mode, day, server.now_iso())
        recovery.atomic_json(stamp, {"completedAt": server.now_iso(), "notification": notification, "selection": selected})
        attempt.update(status="delivered", completedAt=server.now_iso())
    except Exception as exc:
        unsent = attempt["status"] == "preparing" or known_not_sent(exc)
        attempt.update(status="failed_not_sent" if unsent else "outcome_unknown", error=safe_error(exc), failedAt=server.now_iso())
        if unsent:
            attempt["nextRetryAt"] = (now + dt.timedelta(seconds=DIGEST_RETRY_SECONDS)).isoformat()
        digest.transition(state, journal, selected, attempt["status"], mode, day, server.now_iso())
    recovery.atomic_json(attempt_path, attempt)
    return {"attempted": True, **{key: attempt[key] for key in ("status", "attempts", "nextRetryAt", "error") if key in attempt}}


def due_digests(mode, state, now, force=False):
    local = now.astimezone(ZoneInfo("America/Bogota"))
    minute = local.hour * 60 + local.minute
    modes = list(DIGEST_MINUTES) if mode == "polling_30m" else [mode]
    results = {}
    for candidate in modes:
        if minute < DIGEST_MINUTES[candidate] and not (force and mode == candidate):
            continue
        try:
            with recovery.lock(server, "digest-delivery"):
                result = deliver_digest(candidate, state, now)
        except Exception as exc:
            result = {"status": "unavailable", "error": safe_error(exc), "attempted": True}
        results[candidate] = result
        # At most one due mode is attempted per invocation. Already satisfied or
        # waiting modes can be skipped so another due digest is not starved.
        if result["attempted"]:
            break
    return results


def persist_status(state, attempt, archive=True):
    attempt["observedAt"] = server.now_iso()
    recovery.atomic_json(state / f"schedule-status-{attempt['mode']}.json", attempt)
    recovery.atomic_json(state / "schedule-status.json", attempt)
    if archive:
        recovery.atomic_json(state / "schedule_runs" / f"{attempt['attemptId']}.json", attempt)


def run(mode, force=False, invoke=subprocess.run, enabled_source="direct"):
    state = recovery.root(server)
    attempt = {"attemptId": uuid.uuid4().hex, "mode": mode, "startedAt": server.now_iso(), "status": "running",
               "enabled": True, "enabledSource": enabled_source, "intakeStatus": "not_started"}
    # Both digests share a delivery lock, independent of intake. A still-running
    # intake batch must not hold useful notifications behind its model timeout.
    attempt["digests"] = due_digests(mode, state, server.now_utc(), force)
    persist_status(state, attempt, archive=False)
    if mode != "polling_30m":
        pending = any(result["status"] not in {"delivered", "already_recorded", "quiet"} for result in attempt["digests"].values())
        quiet = bool(attempt["digests"]) and all(result["status"] == "quiet" for result in attempt["digests"].values())
        attempt.update(status="incomplete" if pending else "quiet" if quiet else "completed" if attempt["digests"] else "unchanged",
                       intakeStatus="not_requested", digestPending=pending, completedAt=server.now_iso())
        persist_status(state, attempt)
        print(json.dumps({key: attempt[key] for key in ("attemptId", "mode", "status", "intakeStatus", "digests")}))
        return 1 if pending else 0
    with recovery.lock(server, "scheduled-run"):
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
                response = invoke([binary, "--profile", os.environ.get("OPENCLAW_PROFILE", "owlswatch"), "agent", "--agent", "correo", "--session-id", f"correo-{scan['scanId']}-{attempt['attemptId'][:8]}", "--thinking", "low", "--timeout", "600", "--message", prompt], timeout=660, check=False)
                result_code = response.returncode
            result = recovery.finalize(server, scan["scanId"], result_code)
            if not result["ok"]:
                attempt.update(status="incomplete", intakeStatus="incomplete", pendingCount=result["pendingCount"])
            else:
                status = "completed" if scan["candidates"] else "unchanged"
                attempt.update(status=status, intakeStatus=status, completedAt=server.now_iso())
            if any(result["status"] not in {"delivered", "already_recorded", "quiet"} for result in attempt["digests"].values()):
                attempt["status"] = "incomplete"
                attempt["digestPending"] = True
        except Exception as exc:
            attempt.update(status="failed", intakeStatus="failed", error=safe_error(exc), failedAt=server.now_iso())
        finally:
            persist_status(state, attempt)
            print(json.dumps({key: attempt[key] for key in ("attemptId", "mode", "status", "intakeStatus", "changedCount", "pendingCount", "digests", "error") if key in attempt}))
        return 0 if attempt["status"] in {"completed", "unchanged"} else 1


def record_skip(mode, status, enabled, source):
    state = recovery.root(server)
    # A pause observation must not wait behind an already-running model batch.
    # This does not cancel that batch or change any intake/delivery receipt.
    with recovery.lock(server, "schedule-status"):
        attempt = {"attemptId": uuid.uuid4().hex, "mode": mode, "startedAt": server.now_iso(), "status": status,
                   "enabled": enabled, "enabledSource": source, "intakeStatus": "skipped"}
        persist_status(state, attempt)
        print(json.dumps({"mode": mode, "status": status, "enabled": enabled, "enabledSource": source}))
    return 0  # An intentional pause is observable, not a successful mailbox check.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["polling_30m", "daily_summary", "unanswered_7d"])
    parser.add_argument("--force", action="store_true", help="Bypass enable/time gates; never resend a recorded daily digest")
    args = parser.parse_args()
    enabled = Path(os.environ.get("ENABLED_FILE", "~/.openclaw-owlswatch/email-agent.enabled")).expanduser()
    source = "force" if args.force else "environment" if os.environ.get("OWLSWATCH_EMAIL_AGENT_ENABLED") == "1" else "marker" if enabled.exists() else "none"
    if source == "none":
        return record_skip(args.mode, "disabled", False, source)
    if not args.force and args.mode != "polling_30m":
        local = server.now_utc().astimezone(ZoneInfo("America/Bogota"))
        if local.hour * 60 + local.minute < DIGEST_MINUTES[args.mode]:
            return record_skip(args.mode, "before_schedule", True, source)
    return run(args.mode, args.force, enabled_source=source)


if __name__ == "__main__":
    sys.exit(main())
