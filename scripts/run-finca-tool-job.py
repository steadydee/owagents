#!/usr/bin/env python3
"""Run a fixed Finca schedule with durable Telegram delivery checkpoints."""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile


BOGOTA = dt.timezone(dt.timedelta(hours=-5))
JOBS = {"report": 7 * 60, "checkin": 16 * 60}
CHECKIN_TEXT = "Buenas tardes. ¿En qué tareas avanzamos hoy?"
MAX_ATTEMPTS = 3


def atomic_write(path, text):
    descriptor, temporary = tempfile.mkstemp(prefix=".delivery-", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def summary(job, success, code, *, sent=0, retryable=False):
    return {"job": "finca-daily-" + job, "success": success, "code": code,
            "sent": sent, "retryable": retryable}


def load_server(path):
    spec = importlib.util.spec_from_file_location("finca_schedule_server", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_state(state, job, day):
    if not isinstance(state, dict) or state.get("version") != 1 or state.get("job") != job or state.get("date") != day:
        raise ValueError("Invalid journal")
    if not isinstance(state.get("destinationDigest"), str) or len(state["destinationDigest"]) != 64:
        raise ValueError("Invalid destination")
    messages = state.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 20:
        raise ValueError("Invalid messages")
    for row in messages:
        if not isinstance(row, dict) or not isinstance(row.get("text"), str) or not 1 <= len(row["text"]) <= 4096:
            raise ValueError("Invalid message")
        if row.get("digest") != digest(row["text"]) or row.get("status") not in {"pending", "attempting", "sent", "not_sent", "unknown"}:
            raise ValueError("Invalid message state")
        if type(row.get("attempts")) is not int or not 0 <= row["attempts"] <= MAX_ATTEMPTS:
            raise ValueError("Invalid attempts")
        if row["status"] == "sent" and (type(row.get("messageId")) is not int or row["messageId"] <= 0):
            raise ValueError("Missing receipt")
        if row["status"] == "not_sent" and type(row.get("retryable")) is not bool:
            raise ValueError("Invalid retry state")
    return state


def run_job(job, *, profile, server_path, stamp_dir=None, enabled_file=None, force=False, now=None, server=None):
    now = now or dt.datetime.now(BOGOTA)
    now = now.astimezone(BOGOTA)
    day = now.date().isoformat()
    enabled_file = enabled_file or profile / ("daily-" + job + ".enabled")
    stamp_dir = stamp_dir or profile / "schedule-stamps"
    stamp = stamp_dir / ("finca-daily-" + job + "-" + day + ".stamp")
    if not force and (not enabled_file.is_file() or now.hour * 60 + now.minute < JOBS[job]):
        return summary(job, True, "not_due")
    state_dir = profile / "schedule-state" / ("finca-daily-" + job)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_dir.chmod(0o700)
    # One lock per job also excludes a previous day's slow/manual invocation.
    descriptor = os.open(state_dir / "delivery.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return summary(job, False, "operation_in_progress", retryable=True)
        # Legacy stamps remain authoritative, including with --force. Force can
        # bypass time/enable gates, never delivery history or the process lock.
        if stamp.exists():
            return summary(job, True, "already_completed")
        journal = state_dir / (day + ".json")
        state = None
        if journal.exists():
            try:
                state = validate_state(json.loads(journal.read_text()), job, day)
            except (ValueError, TypeError, OSError):
                return summary(job, False, "journal_invalid")
            sent = sum(row["status"] == "sent" for row in state["messages"])
            # A crash after submitting a request cannot prove non-delivery.
            for row in state["messages"]:
                if row["status"] == "attempting":
                    row["status"] = "unknown"
                    atomic_write(journal, json.dumps(state, ensure_ascii=False))
            if any(row["status"] == "unknown" for row in state["messages"]):
                return summary(job, False, "delivery_outcome_unknown", sent=sent)
        server = server or load_server(server_path)
        config = server.load_config()
        destination = server.notify_chat_id(config)
        # Resolve required configuration before recording an attempted send.
        server.telegram_token(config)
        if state is None:
            if job == "report":
                report = server.operations_tool(config, "operations.finca.daily_report", {"timezone": "America/Bogota"})
                messages = report.get("messages") if isinstance(report, dict) else None
                if not isinstance(messages, list) or not 1 <= len(messages) <= 20 or any(not isinstance(text, str) for text in messages):
                    return summary(job, False, "invalid_report")
                messages = [server.worker_safe_report_message(text) for text in messages]
            else:
                messages = [CHECKIN_TEXT]
            state = {"version": 1, "job": job, "date": day, "destinationDigest": digest(destination),
                     "messages": [{"text": text, "digest": digest(text), "status": "pending", "attempts": 0} for text in messages]}
            try:
                validate_state(state, job, day)
            except (ValueError, TypeError):
                return summary(job, False, "invalid_report")
            atomic_write(journal, json.dumps(state, ensure_ascii=False))
        elif state["destinationDigest"] != digest(destination):
            return summary(job, False, "destination_changed")
        for row in state["messages"]:
            sent = sum(item["status"] == "sent" for item in state["messages"])
            if row["status"] == "sent":
                continue
            if row["status"] == "not_sent" and (not row["retryable"] or row["attempts"] >= MAX_ATTEMPTS):
                return summary(job, False, "delivery_rejected", sent=sent)
            row["status"] = "attempting"
            row["attempts"] += 1
            atomic_write(journal, json.dumps(state, ensure_ascii=False))
            try:
                result = server.telegram_send(config, destination, row["text"])
            except Exception as exc:
                confirmed = getattr(exc, "delivery_status", None) == "not_sent"
                row["status"] = "not_sent" if confirmed else "unknown"
                row["retryable"] = confirmed and getattr(exc, "retryable", False) is True
                atomic_write(journal, json.dumps(state, ensure_ascii=False))
                return summary(job, False, "delivery_rejected" if confirmed else "delivery_outcome_unknown",
                               sent=sent, retryable=row["retryable"] and row["attempts"] < MAX_ATTEMPTS)
            if not isinstance(result, dict) or result.get("ok") is not True or type(result.get("messageId")) is not int or result["messageId"] <= 0:
                row["status"] = "unknown"
                atomic_write(journal, json.dumps(state, ensure_ascii=False))
                return summary(job, False, "delivery_outcome_unknown", sent=sent)
            row["status"] = "sent"
            row["messageId"] = result["messageId"]
            atomic_write(journal, json.dumps(state, ensure_ascii=False))
        stamp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write(stamp, now.strftime("%Y-%m-%d %H:%M:%S\n"))
        return summary(job, True, "completed", sent=len(state["messages"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=JOBS)
    parser.add_argument("--force", action="store_true", help="Bypass time/enable gates, never delivery history")
    args = parser.parse_args()
    profile = Path(os.environ.get("FINCA_PROFILE_DIR", "~/.openclaw-finca")).expanduser()
    workspace = Path(os.environ.get("FINCA_WORKSPACE", "~/.openclaw/workspace-finca-ops")).expanduser()
    server_path = Path(os.environ.get("FINCA_TOOL_SERVER", str(workspace / "tools/finca_tasks/server.py"))).expanduser()
    os.environ.setdefault("OPENCLAW_STATE_DIR", str(profile))
    os.environ.setdefault("OPENCLAW_CONFIG_PATH", str(profile / "openclaw.json"))
    try:
        # Tool/provider details and report content do not belong in schedule logs.
        with open(os.devnull, "w") as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            result = run_job(args.job, profile=profile, server_path=server_path, force=args.force,
                             stamp_dir=Path(os.environ["STAMP_DIR"]).expanduser() if os.environ.get("STAMP_DIR") else None,
                             enabled_file=Path(os.environ["ENABLED_FILE"]).expanduser() if os.environ.get("ENABLED_FILE") else None)
    except Exception:
        result = summary(args.job, False, "schedule_job_failed")
    print(json.dumps(result))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
