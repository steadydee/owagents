#!/usr/bin/env python3
"""Local-only, content-free operational report. Never sends messages or restarts."""
import argparse
import datetime as dt
import fcntl
import html
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

PROFILES = ("owlswatch", "hotel", "bailey-finance")
PAUSED_PROFILES = ("nomina",)
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("agent_health", HERE / "agent-health.py")
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)
OUTCOMES = {"expense_draft_created", "quote_draft_created", "billing_draft_created", "reservation_created", "email_intake_processed"}


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as stream:
        os.chmod(temp, 0o600)
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)
    parent = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def estimate(usage, rates):
    rate = rates.get(f"{usage.get('provider')}/{usage.get('model')}")
    if not isinstance(rate, dict):
        return None
    values = []
    for bound in ("low", "high"):
        total = 0
        for field in ("input", "output", "cacheRead", "cacheWrite"):
            count = usage.get(field, 0)
            price = rate.get(bound, {}).get(field)
            if not finite(count) or (count and not finite(price)):
                return None
            total += count * (price or 0) / 1000000
        values.append(total)
    return values if values[0] <= values[1] else None


def report_profile(directory, now, days, rates, previous=None):
    latest, usage, outcomes, oldest = {}, {}, {}, None
    for path in (directory / "health/runs.jsonl.previous", directory / "health/runs.jsonl"):
        if not path.is_file() or path.stat().st_size > 20000000:
            continue
        with path.open(errors="replace") as stream:
            for line in stream:
                if len(line) > 1000000:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(item, dict):
                    continue
                at = health.timestamp(item.get("at"))
                run = health.correlation(item.get("runKey"))
                if at is None or not run or at > now + 60:
                    continue
                oldest = at if oldest is None else min(oldest, at)
                if at < now - days * 86400:
                    continue
                kind = item.get("kind")
                if kind == "run_end":
                    order = (at, health.count(item.get("revision")))
                    if run not in latest or order > latest[run][0]:
                        latest[run] = (order, item)
                elif kind == "usage" and health.correlation(item.get("usageKey")):
                    usage[(run, item["usageKey"])] = item
                elif kind == "business_outcome" and health.correlation(item.get("taskKey")) and item.get("outcome") in OUTCOMES:
                    outcomes[item["taskKey"]] = item
    agents = {}
    def row(agent):
        return agents.setdefault(health.safe_agent(agent), {"completedResponses": 0, "completedRuns": 0, "responseObservationCoverage": 0, "failedRuns": 0, "verifiedBusinessOutcomes": 0,
            "outcomeTypes": {}, "tokens": 0, "usageAttempts": 0, "pricedAttempts": 0, "estimatedCostUsdRange": [0.0, 0.0], "durationsMs": []})
    for _, item in latest.values():
        group = row(item.get("agent"))
        ok = item.get("success") is True and not item.get("code")
        group["completedRuns" if ok else "failedRuns"] += 1
        group["responseObservationCoverage"] += isinstance(item.get("responseCompleted"), bool)
        if ok and item.get("responseCompleted") is True:
            group["completedResponses"] += 1
            group["durationsMs"].append(health.count(item.get("durationMs")))
    for item in usage.values():
        group = row(item.get("agent"))
        group["usageAttempts"] += 1
        group["tokens"] += health.count(item.get("tokens"))
        cost = estimate(item, rates)
        if cost is not None:
            group["pricedAttempts"] += 1
            group["estimatedCostUsdRange"] = [group["estimatedCostUsdRange"][i] + cost[i] for i in (0, 1)]
    for item in outcomes.values():
        group = row(item.get("agent"))
        group["verifiedBusinessOutcomes"] += 1
        kind = item["outcome"]
        group["outcomeTypes"][kind] = group["outcomeTypes"].get(kind, 0) + 1
    for group in agents.values():
        durations = sorted(group.pop("durationsMs"))
        group["responseP50Seconds"] = durations[len(durations) // 2] / 1000 if durations else None
        group["responseP95Seconds"] = durations[min(len(durations) - 1, math.ceil(len(durations) * .95) - 1)] / 1000 if durations else None
        complete = bool(group["usageAttempts"]) and group["pricedAttempts"] == group["usageAttempts"]
        group["costCoverageComplete"] = complete
        group["estimatedCostUsdRange"] = [round(value, 8) for value in group["estimatedCostUsdRange"]] if group["pricedAttempts"] else None
        group["estimatedCostPerVerifiedOutcomeUsdRange"] = [round(value / group["verifiedBusinessOutcomes"], 8) for value in group["estimatedCostUsdRange"]] if complete and group["verifiedBusinessOutcomes"] else None
        group["humanReviewMinutes"] = None
    return {"health": health.inspect_runtime(directory, now, previous=previous), "agents": agents, "requestedDays": days,
            "oldestRetainedRecordAt": dt.datetime.fromtimestamp(oldest, dt.timezone.utc).isoformat() if oldest else None,
            "businessOutcomeCoverage": "Explicit positive tool receipts only; not final human approval, delivery, or all business paths.",
            "costBasis": "Configured rate ranges; missing rates are unknown, never zero. Excludes external tool fees and human review."}


def balance_runtime(profile, home=None):
    """Only activation may select the runtime; staged releases are not active."""
    if profile not in PROFILES:
        raise ValueError("Invalid profile")
    home = Path(home) if home is not None else Path.home()
    directory = home / (".openclaw-" + profile)
    runtime = directory / "runtime"
    path = runtime / "active.json"
    if not path.exists():
        if path.is_symlink():
            raise ValueError("Invalid runtime metadata")
        return None
    if any(p.is_symlink() for p in (directory, runtime, path)) or not path.is_file():
        raise ValueError("Invalid runtime metadata")
    metadata = path.stat()
    if metadata.st_mode & 0o077 or metadata.st_uid != os.getuid() or metadata.st_size > 16384:
        raise ValueError("Private runtime metadata required")
    record = json.loads(path.read_text())
    if not isinstance(record, dict) or record.get("schemaVersion") != 1 or record.get("profile") != profile:
        raise ValueError("Runtime profile mismatch")
    for name in ("nodePath", "openclawPackage"):
        if not isinstance(record.get(name), str) or not Path(record[name]).is_absolute():
            raise ValueError("Absolute runtime paths required")
    node, package = (Path(record[name]).resolve() for name in ("nodePath", "openclawPackage"))
    if record.get("legacy") is True:
        if package != (home / ".npm-global/lib/node_modules/openclaw").resolve():
            raise ValueError("Unreviewed legacy runtime")
        if any(part.startswith(".openclaw") for part in node.parts):
            raise ValueError("Legacy Node belongs to a profile")
    else:
        releases = (runtime / "releases").resolve()
        if not node.is_relative_to(releases) or not package.is_relative_to(releases):
            raise ValueError("Runtime escapes selected profile")
        if node.parent.name != "bin" or node.parent.parent.name != "node" or package.parent.name != "node_modules":
            raise ValueError("Unknown runtime layout")
        if node.parents[2] != package.parents[1]:
            raise ValueError("Runtime generation mismatch")
    if not node.is_file() or not os.access(node, os.X_OK) or not package.is_dir():
        raise ValueError("Runtime files missing")
    package_metadata = json.loads((package / "package.json").read_text())
    if not isinstance(package_metadata, dict) or package_metadata.get("name") != "openclaw":
        raise ValueError("Wrong SDK package")
    if not (package / "dist/plugin-sdk/provider-auth-runtime.js").is_file():
        raise ValueError("Auth SDK missing")
    return {"nodePath": str(node), "openclawPackage": str(package)}


def default_budget_state(home=None):
    return (Path(home) if home is not None else Path.home()) / "Library/Application Support/OwlswatchOperations/monitor-state/http-budget.sqlite3"


def check_balance(profile, state, now, runner=subprocess.run, home=None, budget_state=None):
    previous = state.get("balances", {}).get(profile, {})
    if now < previous.get("nextCheckAt", 0):
        return previous
    try:
        runtime = balance_runtime(profile, home)
    except (OSError, ValueError, TypeError, RuntimeError):
        runtime = None
        value = {"status": "runtime_invalid"}
    else:
        value = {"status": "runtime_unconfigured"}
    if runtime:
        try:
            env = dict(os.environ)
            for name in ("NODE_OPTIONS", "NODE_PATH"):
                env.pop(name, None)
            if home is not None:
                env["HOME"] = str(home)
            output = runner([runtime["nodePath"], str(HERE / "check-deepseek-balance.mjs"), profile,
                             "--sdk-root", runtime["openclawPackage"], "--budget-state", str(budget_state or default_budget_state(home)),
                             "--budget-python", sys.executable], env=env, capture_output=True, text=True, timeout=20)
            value = json.loads(output.stdout) if output.returncode == 0 else {"status": "helper_failed"}
            if not isinstance(value, dict) or value.get("status") not in {"ok", "credential_unavailable", "provider_error", "check_failed", "helper_failed", "runtime_unconfigured", "budget_exhausted", "budget_unavailable", "budget_unconfigured"}:
                raise ValueError()
        except (OSError, ValueError, subprocess.TimeoutExpired):
            value = {"status": "check_failed"}
    return {**value, "checkedAt": now, "nextCheckAt": now + 3600}


def balance_alert(profile, balance):
    status = balance.get("status", "")
    if status.startswith("budget_"):
        return "monitor:" + status
    if status != "ok":
        return profile + ":balance_unverified"
    if balance.get("available") is False or any(item["currency"] == "USD" and item["total"] < 5 for item in balance.get("balances", [])):
        return profile + ":provider_balance_low"
    return None


def budget_status(path, runner=subprocess.run):
    try:
        result = runner([sys.executable, str(HERE / "monitor-http-budget.py"), "status", "--state", str(path)],
                        capture_output=True, text=True, timeout=10)
        data = json.loads(result.stdout)
        if result.returncode or not isinstance(data, dict) or data.get("status") != "ok":
            raise ValueError("Budget status unavailable")
        return data
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"status": "budget_unavailable"}


def load_outcome_state(path):
    """An unreadable retained ledger must never look like an empty ledger."""
    if not path.exists() and not path.is_symlink():
        return {}, True
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 20_000_000:
            raise ValueError("Invalid retained outcome state")
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or data.get("schemaVersion") != 1 or not isinstance(data.get("profiles"), dict):
            raise ValueError("Invalid retained outcome state")
        for profile, evidence in data["profiles"].items():
            if profile not in PROFILES + PAUSED_PROFILES or not isinstance(evidence, dict) or not isinstance(evidence.get("outcomeEvidence"), list):
                raise ValueError("Invalid retained outcome state")
            if not health.validate_outcome_evidence(evidence["outcomeEvidence"]):
                raise ValueError("Invalid retained outcome evidence")
        return data["profiles"], True
    except (OSError, ValueError, TypeError):
        return {}, False


def acquire_monitor_lock(lock, wait_for_balance=False, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + (60 if wait_for_balance else 0)
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if not wait_for_balance or clock() >= deadline:
                return False
            sleep(0.1)


def preserve_task_alerts(previous):
    return [code for code in previous if isinstance(code, str) and len(code) <= 200
            and code.split(":", 1)[0] in PROFILES
            and any(marker in code for marker in (":task_pending_review:", ":agent_blocked:", ":agent_unfinished:", ":run_unfinished:"))]


def transition_alerts(previous, current, now, correo=None):
    before = set(previous)
    after = set(current)
    events = [{"at": now, "state": "opened", "code": code} for code in sorted(after - before)]
    for code in sorted(before - after):
        state = "monitoring_paused" if code.split(":", 1)[0] in PAUSED_PROFILES else "resolved"
        digest = re.fullmatch(r"owlswatch:correo_schedule:(daily_summary|unanswered_7d):(\d{4}-\d{2}-\d{2}):[a-z_]+", code)
        if digest:
            mode, day = digest.groups()
            row = (correo or {}).get("digests", {}).get(mode, {})
            if row.get("expectedDay") != day:
                state = "schedule_window_elapsed" if row.get("expectedDay") else "evidence_unavailable"
            elif row.get("state") != "delivered":
                state = "evidence_changed"  # A new status is not a delivery receipt.
        elif code.startswith("owlswatch:correo_schedule:"):
            positive = False
            if correo:
                suffix = code.removeprefix("owlswatch:correo_schedule:")
                if suffix.startswith("poll_"):
                    positive = correo.get("polling", {}).get("state") == "attempt_completed"
                elif suffix.startswith("checkpoint_"):
                    positive = correo.get("polling", {}).get("checkpoint", {}).get("state") == "current"
                elif suffix in {"disabled", "enablement_unverified"}:
                    positive = correo.get("enablement", {}).get("state") in {"enabled_by_marker", "enabled_observed"}
            state = "resolved" if positive else "evidence_changed" if correo else "evidence_unavailable"
        events.append({"at": now, "state": state, "code": code})
    return events


def recovery_status(directory, now):
    candidates = []
    for path in directory.glob("*.verification.json"):
        data = health.load_json(path, {})
        if isinstance(data, dict) and data.get("status") == "passed" and finite(data.get("at")) and data.get("at") <= now + 60:
            archive = path.with_suffix("").with_suffix(".owlrec")
            if archive.is_file():
                candidates.append(data)
    if not candidates:
        return {"status": "unverified", "offDeviceRecoveryKeyVerified": False}
    latest = max(candidates, key=lambda item: item["at"])
    return {"status": "fresh" if now - latest["at"] <= 36 * 3600 else "stale", "lastVerifiedAt": latest["at"],
            "verifiedFiles": health.count(latest.get("verifiedFiles")), "profiles": latest.get("profiles", []),
            "offDeviceRecoveryKeyVerified": False}


# Fixed local paths, bounded reads, and positive receipts only. Schedule evidence
# deliberately does not change gateway availability or resolve task incidents.
CORREO_INTERVAL = 1800
CORREO_GRACE = 900  # Includes the 660-second bounded intake run and monitor tick.
CORREO_DIGESTS = {"daily_summary": (8, 0), "unanswered_7d": (8, 15)}
CORREO_STATUSES = {"running", "completed", "unchanged", "quiet", "incomplete", "failed", "disabled", "before_schedule"}
CORREO_DELIVERY = {"preparing", "sending", "delivered", "failed_not_sent", "outcome_unknown",
                   "already_recorded", "quiet", "attempt_limit", "retry_wait", "unavailable"}
CORREO_ERRORS = {"runtime_missing", "config_missing", "config_invalid", "invalid_input", "http_error",
                 "telegram_error", "digest_outcome_unknown", "notification_not_confirmed", "internal_error"}


def schedule_path_safe(path, home):
    try:
        path.relative_to(home)
        while path != home:
            if path.is_symlink():
                return False
            path = path.parent
        return True
    except (OSError, ValueError):
        return False


def local_schedule_json(path, home):
    """Never read message/task archives or echo file content in diagnostics."""
    try:
        if not schedule_path_safe(path, home):
            return {}, "unavailable"
        if not path.is_file() or path.stat().st_size > 262144:
            return {}, "unavailable" if path.exists() or path.is_symlink() else "missing"
        value = json.loads(path.read_text())
        return (value, "present") if isinstance(value, dict) else ({}, "invalid")
    except (OSError, ValueError, UnicodeError):
        return {}, "unavailable"


def schedule_time(value, now):
    at = health.timestamp(value)
    return at if at is not None and 0 <= at <= now + 60 else None


def schedule_error(value):
    code = value.get("code") if isinstance(value, dict) else None
    return code if isinstance(code, str) and code in CORREO_ERRORS else "recorded_error"


def schedule_record(value, mode, now):
    if value.get("mode") != mode or (not isinstance(value.get("status"), str) or value["status"] not in CORREO_STATUSES):
        return None
    started = schedule_time(value.get("startedAt"), now)
    observed = schedule_time(value.get("observedAt", value.get("completedAt", value.get("failedAt", value.get("startedAt")))), now)
    if started is None or observed is None or observed < started:
        return None
    result = {"status": value["status"], "startedAt": health.iso(started), "observedAt": health.iso(observed),
              "ageSeconds": max(0, int(now - observed))}
    for field in ("completedAt", "failedAt"):
        if field in value:
            at = schedule_time(value[field], now)
            if at is None or not started <= at <= observed:
                return None
            result[field] = health.iso(at)
    if isinstance(value.get("intakeStatus"), str) and value["intakeStatus"] in CORREO_STATUSES | {"not_started", "not_requested", "skipped"}:
        result["intakeStatus"] = value["intakeStatus"]
    if isinstance(value.get("enabled"), bool):
        result["enabled"] = value["enabled"]
    if isinstance(value.get("enabledSource"), str) and value["enabledSource"] in {"marker", "environment", "force", "direct", "none"}:
        result["enabledSource"] = value["enabledSource"]
    if isinstance(value.get("pendingCount"), int) and not isinstance(value["pendingCount"], bool) and value["pendingCount"] >= 0:
        result["pendingCount"] = value["pendingCount"]
    if value.get("error"):
        result["errorCode"] = schedule_error(value["error"])
    digests = value.get("digests")
    if isinstance(digests, dict):
        result["digests"] = {key: {"status": entry["status"]} for key, entry in digests.items()
                             if key in CORREO_DIGESTS and isinstance(entry, dict) and isinstance(entry.get("status"), str) and entry["status"] in CORREO_DELIVERY}
    return result


def correo_schedule(home, now):
    """Summarize the default Correo schedule from a constant number of local files."""
    home = home.resolve()
    state = home / ".openclaw/workspace-owlswatch-correo/tasks/email_runtime"
    profile = home / ".openclaw-owlswatch"
    zone = ZoneInfo("America/Bogota")
    local = dt.datetime.fromtimestamp(now, zone)
    marker_at = None
    try:
        marker = profile / "email-agent.enabled"
        marker_state = "unavailable" if not schedule_path_safe(marker, home) else "present" if marker.is_file() else "absent"
        if marker_state == "present":
            marker_at = marker.stat().st_mtime
            if not 0 <= marker_at <= now + 60:
                marker_state, marker_at = "unavailable", None
        elif marker_state != "unavailable" and marker.exists():
            marker_state = "unavailable"
    except OSError:
        marker_state = "unavailable"
    legacy, _ = local_schedule_json(state / "schedule-status.json", home)
    records, sources = {}, {}
    for mode in ("polling_30m", *CORREO_DIGESTS):
        value, source = local_schedule_json(state / f"schedule-status-{mode}.json", home)
        if source == "missing" and legacy.get("mode") == mode:
            value, source = legacy, "legacy_latest"
        record = schedule_record(value, mode, now)
        records[mode], sources[mode] = record, source if record else ("missing" if source == "missing" else "invalid")
    fresh = [record for record in records.values() if record and record["ageSeconds"] <= CORREO_INTERVAL + CORREO_GRACE]
    latest = max(fresh, key=lambda record: record["observedAt"]) if fresh else None
    # A missing marker is not proof of disabled: the runner also supports an
    # environment gate. Only its fresh explicit disabled observation says so.
    enabled = "enabled_by_marker" if marker_state == "present" else "unknown"
    if latest and latest.get("enabledSource") == "environment" and latest.get("enabled") is True:
        enabled = "enabled_observed"
    elif latest and latest["status"] == "disabled" and (marker_at is None or health.timestamp(latest["observedAt"]) > marker_at):
        enabled = "disabled_observed"
    startup = marker_at is not None and now - marker_at <= CORREO_INTERVAL + CORREO_GRACE
    polling = {"state": "unknown", "intervalSeconds": CORREO_INTERVAL, "graceSeconds": CORREO_GRACE,
               "evidence": sources["polling_30m"], "lastAttempt": records["polling_30m"]}
    poll = records["polling_30m"]
    if enabled == "disabled_observed":
        polling["state"] = "disabled"
    elif poll and poll["status"] == "disabled":
        polling["state"] = "awaiting_attempt" if startup else "stale_disabled_observation"
    elif poll and poll["ageSeconds"] > CORREO_INTERVAL + CORREO_GRACE:
        polling["state"] = "awaiting_attempt" if startup else "attempt_overdue"
    elif poll:
        status = poll.get("intakeStatus", poll["status"])
        if status in {"completed", "unchanged"} and "completedAt" in poll:
            polling["state"] = "attempt_completed"
        elif status in {"failed", "incomplete"}:
            polling["state"] = status
        elif poll["status"] == "running":
            polling["state"] = "running" if now - health.timestamp(poll["startedAt"]) <= CORREO_GRACE else "attempt_stale"
    elif startup:
        polling["state"] = "awaiting_attempt"
    elif enabled == "enabled_by_marker" and sources["polling_30m"] == "missing":
        polling["state"] = "attempt_overdue"
    checkpoint, checkpoint_source = local_schedule_json(state / "checkpoint.json", home)
    acknowledged = schedule_time(checkpoint.get("lastAcknowledgedAt"), now)
    polling["checkpoint"] = {"state": "unknown", "evidence": checkpoint_source}
    if acknowledged is not None:
        polling["checkpoint"].update(state="current" if now - acknowledged <= CORREO_INTERVAL + CORREO_GRACE else "stale",
                                      lastAcknowledgedAt=health.iso(acknowledged), ageSeconds=max(0, int(now - acknowledged)))
    digests = {}
    for mode, (hour, minute) in CORREO_DIGESTS.items():
        today_due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        due = today_due if local >= today_due else today_due - dt.timedelta(days=1)
        day = due.date().isoformat()
        row = {"state": "unknown", "expectedDay": day, "scheduledAt": due.isoformat(),
               "nextScheduledAt": (today_due + dt.timedelta(days=int(local >= today_due))).isoformat(),
               "graceSeconds": CORREO_GRACE, "lastScheduleObservation": records[mode]}
        receipt, receipt_source = local_schedule_json(state / f"{mode}-{day}.json", home)
        delivered = schedule_time(receipt.get("completedAt"), now)
        notification = receipt.get("notification")
        message_id = notification.get("message_id") if isinstance(notification, dict) else None
        confirmed = (delivered is not None and dt.datetime.fromtimestamp(delivered, zone).date().isoformat() == day
                     and isinstance(notification, dict) and notification.get("ok") is True
                     and isinstance(message_id, int) and not isinstance(message_id, bool) and message_id > 0)
        quiet = (delivered is not None and dt.datetime.fromtimestamp(delivered, zone).date().isoformat() == day
                 and receipt.get("mode") == mode and receipt.get("day") == day
                 and receipt.get("status") == "quiet" and receipt.get("reason") == "no_new_actionable_items"
                 and type(receipt.get("itemCount")) is int and receipt["itemCount"] == 0
                 and "notification" not in receipt)
        row["receiptEvidence"] = receipt_source
        if confirmed:
            row.update(state="delivered", completedAt=health.iso(delivered))
        elif quiet:
            row.update(state="quiet", completedAt=health.iso(delivered), reason="no_new_actionable_items")
        elif receipt_source != "missing":
            row["state"] = "delivery_unverified"
        else:
            old_name = "correo-daily-summary" if mode == "daily_summary" else "correo-unanswered"
            old_stamp = profile / "schedule-stamps" / f"{old_name}-{day}.stamp"
            try:
                legacy_exists = not schedule_path_safe(old_stamp, home) or old_stamp.exists()
            except OSError:
                legacy_exists = True  # Unreadable historical evidence cannot prove a miss.
            attempt, attempt_source = local_schedule_json(state / "digest-attempts" / f"{mode}-{day}.json", home)
            at = schedule_time(attempt.get("startedAt"), now)
            status = attempt.get("status")
            valid_attempt = (attempt.get("mode") == mode and attempt.get("day") == day and at is not None
                             and dt.datetime.fromtimestamp(at, zone).date().isoformat() == day and isinstance(status, str) and status in CORREO_DELIVERY)
            if legacy_exists:
                row["state"] = "legacy_stamp_unverified"
            elif valid_attempt:
                row["lastAttempt"] = {"status": status, "startedAt": health.iso(at)}
                attempts = attempt.get("attempts")
                if isinstance(attempts, int) and not isinstance(attempts, bool) and attempts >= 0:
                    row["lastAttempt"]["attempts"] = attempts
                if attempt.get("error"):
                    row["lastAttempt"]["errorCode"] = schedule_error(attempt["error"])
                retry = health.timestamp(attempt.get("nextRetryAt"))
                if retry is not None and at <= retry <= at + 86400:
                    row["lastAttempt"]["nextRetryAt"] = health.iso(retry)
                row["state"] = ("delivery_unknown" if status in {"sending", "outcome_unknown", "delivered"} else
                                "attempt_in_progress" if status == "preparing" and now - at <= CORREO_GRACE else
                                "attempt_stale" if status == "preparing" else
                                "retry_wait" if status == "failed_not_sent" and retry is not None and now < retry else
                                "failed_not_sent" if status == "failed_not_sent" else "delivery_unverified")
            elif attempt_source != "missing":
                row["state"] = "unknown"
            else:
                observations = [record for record in records.values() if record
                                and health.timestamp(record["startedAt"]) >= due.timestamp()
                                and mode in record.get("digests", {})]
                observed = max(observations, key=lambda record: record["observedAt"]) if observations else None
                if observed and observed["digests"][mode]["status"] == "unavailable":
                    row["state"] = "failed"
                    row["lastAttempt"] = {"status": "unavailable", "startedAt": observed["startedAt"]}
                elif enabled == "disabled_observed":
                    row["state"] = "disabled"
                elif startup or now <= due.timestamp() + CORREO_GRACE:
                    row["state"] = "awaiting_receipt"
                elif enabled in {"enabled_by_marker", "enabled_observed"}:
                    row["state"] = "overdue"
        digests[mode] = row
    return {"evidenceScope": "Default local Correo paths only; no remote checks or launchd ownership verification.",
            "timeZone": "America/Bogota", "enablement": {"state": enabled, "marker": marker_state,
            "lastObservation": {key: latest[key] for key in ("observedAt", "enabled", "enabledSource") if key in latest} if latest else None},
            "polling": polling, "digests": digests,
            "coverage": "Attempt completion is not mailbox completeness. Quiet means no new verified actionable items qualified for a notice, not an empty inbox or a delivery. Checkpoint age and digest delivery are independent; a delivered digest does not complete intake."}


def correo_schedule_alerts(schedule):
    """Local operational advisories only; never business-task resolutions."""
    prefix = "owlswatch:correo_schedule:"
    codes = []
    enabled = schedule["enablement"]["state"]
    if enabled == "disabled_observed":
        codes.append(prefix + "disabled")
    elif enabled == "unknown":
        codes.append(prefix + "enablement_unverified")
    polling = schedule["polling"]
    poll_code = {"unknown": "poll_unverified", "failed": "poll_failed", "incomplete": "poll_incomplete",
                 "attempt_overdue": "poll_overdue", "attempt_stale": "poll_stale",
                 "stale_disabled_observation": "poll_unverified"}.get(polling["state"])
    if poll_code:
        codes.append(prefix + poll_code)
    if polling["checkpoint"]["state"] in {"stale", "unknown"}:
        codes.append(prefix + "checkpoint_" + ("stale" if polling["checkpoint"]["state"] == "stale" else "unverified"))
    for mode, row in schedule["digests"].items():
        if row["state"] not in {"delivered", "quiet", "awaiting_receipt", "attempt_in_progress"}:
            codes.append(prefix + mode + ":" + row["expectedDay"] + ":" + row["state"])
    return codes


def correo_schedule_html(schedule):
    if not schedule:
        return ""
    rows = []
    polling = schedule["polling"]
    checkpoint = polling["checkpoint"]
    evidence = polling.get("lastAttempt") or {}
    rows.append(("Mailbox polling (every 30 minutes)", polling["state"], evidence.get("observedAt", "Unknown"),
                 "Checkpoint: " + checkpoint["state"] + "; last acknowledged: " + checkpoint.get("lastAcknowledgedAt", "Unknown")))
    for mode, row in schedule["digests"].items():
        detail = "Expected " + row["scheduledAt"] + "; next " + row["nextScheduledAt"]
        attempt = row.get("lastAttempt") or {}
        if attempt.get("errorCode"):
            detail += "; error: " + attempt["errorCode"]
        if attempt.get("nextRetryAt"):
            detail += "; retry eligible: " + attempt["nextRetryAt"]
        rows.append(("Morning summary (08:00)" if mode == "daily_summary" else "Follow-up summary (08:15)",
                     row["state"], row.get("completedAt", attempt.get("startedAt", "Unknown")), detail))
    table = "".join("<tr>" + "".join("<td>" + html.escape(str(cell)) + "</td>" for cell in row) + "</tr>" for row in rows)
    return ("<h2>Correo schedule · Bogotá</h2><p>Enablement: " + html.escape(schedule["enablement"]["state"])
            + "; marker: " + html.escape(schedule["enablement"]["marker"]) + ".</p>"
            + "<table><tr><th>Job</th><th>Local status</th><th>Latest evidence (UTC)</th><th>Timing / evidence</th></tr>" + table + "</table>"
            + "<p>" + html.escape(schedule["coverage"]) + " Disabled, overdue, stale, or unknown schedule evidence does not establish a gateway outage. "
            + "Legacy stamps do not verify delivery. Date-scoped digest alerts that age out are marked schedule_window_elapsed, never resolved without a matching receipt. " + html.escape(schedule["evidenceScope"]) + "</p>")


def html_report(report):
    rows = []
    health_rows = []
    for profile, detail in report["profiles"].items():
        state = detail["health"]
        availability = "; ".join(agent + ": " + value.get("state", "unknown")
            for agent, value in state.get("availability", {}).items() if isinstance(value, dict)) or "unknown"
        health_rows.append("<tr>" + "".join("<td>" + html.escape(str(value)) + "</td>" for value in (
            profile, availability,
            state.get("taskHealth", {}).get("state", "unknown"),
            ", ".join(state.get("outcomeReasons", [])) or "No unresolved evidence recorded; coverage is incomplete")) + "</tr>")
        for agent, value in detail["agents"].items():
            cost = value["estimatedCostPerVerifiedOutcomeUsdRange"]
            rows.append("<tr>" + "".join("<td>" + html.escape(str(item)) + "</td>" for item in (
                profile + "/" + agent, value["completedResponses"], value["failedRuns"], value["verifiedBusinessOutcomes"],
                value["responseP95Seconds"] if value["responseP95Seconds"] is not None else "—", cost if cost else "Unknown")) + "</tr>")
    return """<!doctype html><meta charset="utf-8"><title>Agent operations</title>
<style>body{font:16px system-ui;margin:3rem;max-width:1100px;color:#172330}td,th{padding:.6rem;text-align:left;border-bottom:1px solid #ddd}pre{white-space:pre-wrap}code{background:#eee}</style>
<h1>Agent operations</h1><p>Updated """ + html.escape(report["at"]) + """</p><h2>Active local alerts</h2><pre>""" + html.escape("\n".join(report["activeAlerts"]) or "No active recorded alerts") + """</pre>
<h2>Availability and unfinished work</h2><table><tr><th>Profile</th><th>Availability</th><th>Task status</th><th>Unresolved evidence</th></tr>""" + "".join(health_rows) + """</table>
<p>Old channel snapshots are unverified, not a current outage. A recovered service or successful test does not finish an earlier employee request. Task completion and delivery require their own matching evidence.</p>
""" + correo_schedule_html(report.get("correoSchedule")) + """
<table><tr><th>Agent</th><th>Completed responses</th><th>Failed runs</th><th>Verified outcomes</th><th>P95 seconds</th><th>Estimated USD / outcome</th></tr>""" + "".join(rows) + """</table>
<p>Completed responses require observed assistant text from a successful run; this does not prove channel delivery. Older runs lack this observation. Verified outcomes are explicit tool receipts, not final human approval or delivery. Costs require full known-rate coverage. Unknown is not zero. Human review time is not yet recorded. Retained journals may cover less than the requested period.</p>
<h2>Monitoring HTTP budget</h2><pre>""" + html.escape(json.dumps(report.get("monitorBudget", {"status": "unknown"}), indent=2)) + """</pre><p>This budget limits diagnostics only. Customer work continues when diagnostic permits are exhausted.</p>
<h2>Provider balance checks</h2><pre>""" + html.escape(json.dumps(report["balances"], indent=2)) + "</pre><h2>Encrypted recovery</h2><pre>" + html.escape(json.dumps(report["recovery"], indent=2)) + "</pre>"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", action="append", choices=PROFILES)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--days", type=int, choices=range(1, 31), default=7)
    parser.add_argument("--rates", type=Path, help="Explicit USD per million-token low/high rates; no guessed legacy aliases")
    parser.add_argument("--check-balance", action="store_true")
    parser.add_argument("--budget-state", type=Path, default=default_budget_state(), help="Shared durable monitoring budget; independent of output directory and profile")
    args = parser.parse_args()
    os.umask(0o077)
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.output_dir / "monitor.lock").open("a") as lock:
        if not acquire_monitor_lock(lock, args.check_balance):
            if args.check_balance:
                raise SystemExit("Balance check did not run: local monitor lock remained busy for 60 seconds")
            return
        now = time.time()
        state_path = args.output_dir / "state.json"
        state = health.load_json(state_path, {})
        rates = health.load_json(args.rates, {}) if args.rates else {}
        profiles, balances, alerts = {}, {p: b for p, b in state.get("balances", {}).items() if p in PROFILES}, []
        selected_profiles = args.profile or PROFILES
        # A filtered inspection cannot resolve another profile's old incident.
        alerts.extend(code for code in state.get("activeAlerts", []) if isinstance(code, str) and len(code) <= 200
                      and code.split(":", 1)[0] in set(PROFILES) - set(selected_profiles))
        outcome_path = args.output_dir / "outcomes.json"
        retained, outcome_state_valid = load_outcome_state(outcome_path)
        updated_outcomes = dict(retained)
        if not outcome_state_valid:
            alerts.append("monitor:outcome_state_unavailable")
            alerts.extend(preserve_task_alerts(state.get("activeAlerts", [])))
        for profile in selected_profiles:
            directory = Path.home() / (".openclaw-" + profile)
            try:
                profiles[profile] = report_profile(directory, now, args.days, rates, previous=retained.get(profile))
            except (OSError, ValueError, TypeError):
                outcome_state_valid = False
                profiles[profile] = {"agents": {}, "requestedDays": args.days,
                    "health": {"instrumented": False, "reasons": [], "outcomeReasons": [], "availability": {},
                               "taskHealth": {"state": "unknown", "persistence": "unavailable"}}}
            runtime_health = profiles[profile]["health"]
            if outcome_state_valid:
                updated_outcomes[profile] = {"outcomeEvidence": runtime_health.get("outcomeEvidence", []),
                    "agents": runtime_health.get("agents", {}), "unfinishedRuns": runtime_health.get("unfinishedRuns", {})}
            else:
                runtime_health.setdefault("taskHealth", {}).update(state="unknown", persistence="unavailable")
            # Incident aliases in legacy `reasons` are not proof of an outage.
            alerts.extend(profile + ":" + reason for reason in runtime_health.get("outcomeReasons", []))
            if not profiles[profile]["health"]["instrumented"]:
                alerts.append(profile + ":telemetry_missing")
            if args.check_balance:
                balances[profile] = check_balance(profile, state, now, budget_state=args.budget_state)
            balance = balances.get(profile, {})
            if balance:
                alert = balance_alert(profile, balance)
                if alert:
                    alerts.append(alert)
        if not outcome_state_valid:
            alerts.append("monitor:outcome_state_unavailable")
            alerts.extend(preserve_task_alerts(state.get("activeAlerts", [])))
            for detail in profiles.values():
                detail["health"].setdefault("taskHealth", {})["persistence"] = "unavailable"
        if outcome_state_valid:
            try:
                atomic_json(outcome_path, {"schemaVersion": 1, "profiles": updated_outcomes})
            except (OSError, ValueError):
                alerts.append("monitor:outcome_state_persistence_failed")
                alerts.extend(preserve_task_alerts(state.get("activeAlerts", [])))
                for detail in profiles.values():
                    detail["health"].setdefault("taskHealth", {})["persistence"] = "failed"
        recovery = recovery_status(Path.home() / "Backups/owlswatch-agents/encrypted-runtime", now)
        monitor_budget = budget_status(args.budget_state)
        if monitor_budget.get("status") != "ok":
            alerts.append("monitor:budget_unavailable")
        if recovery["status"] != "fresh":
            alerts.append("recovery:" + recovery["status"])
        elif not set(PROFILES).issubset(recovery["profiles"]):
            alerts.append("recovery:profile_coverage_incomplete")
        report = {"at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(), "profiles": profiles, "balances": balances,
                  "recovery": recovery, "monitorBudget": monitor_budget, "profileScope": list(selected_profiles),
                  "pausedProfiles": list(PAUSED_PROFILES), "activeAlerts": sorted(set(alerts))}
        if "owlswatch" in selected_profiles:
            report["correoSchedule"] = correo_schedule(Path.home(), now)
            report["activeAlerts"] = sorted(set(report["activeAlerts"] + correo_schedule_alerts(report["correoSchedule"])))
        events = transition_alerts(state.get("activeAlerts", []), report["activeAlerts"], now, report.get("correoSchedule"))
        history = health.load_json(args.output_dir / "alerts.json", [])
        atomic_json(args.output_dir / "alerts.json", (history + events)[-2000:])
        atomic_json(args.output_dir / "report.json", report)
        atomic_json(state_path, {"balances": balances, "activeAlerts": report["activeAlerts"]})
        (args.output_dir / "index.html").write_text(html_report(report))
        print(json.dumps({"report": str(args.output_dir / "index.html"), "newTransitions": len(events), "activeAlerts": report["activeAlerts"]}))


if __name__ == "__main__":
    main()
