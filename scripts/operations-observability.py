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


def transition_alerts(previous, current, now):
    before = set(previous)
    after = set(current)
    return ([{"at": now, "state": "opened", "code": code} for code in sorted(after - before)]
            + [{"at": now, "state": "monitoring_paused" if code.split(":", 1)[0] in PAUSED_PROFILES else "resolved", "code": code}
               for code in sorted(before - after)])


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
        events = transition_alerts(state.get("activeAlerts", []), report["activeAlerts"], now)
        history = health.load_json(args.output_dir / "alerts.json", [])
        atomic_json(args.output_dir / "alerts.json", (history + events)[-2000:])
        atomic_json(args.output_dir / "report.json", report)
        atomic_json(state_path, {"balances": balances, "activeAlerts": report["activeAlerts"]})
        (args.output_dir / "index.html").write_text(html_report(report))
        print(json.dumps({"report": str(args.output_dir / "index.html"), "newTransitions": len(events), "activeAlerts": report["activeAlerts"]}))


if __name__ == "__main__":
    main()
