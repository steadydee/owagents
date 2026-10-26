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
import time

PROFILES = ("owlswatch", "hotel", "bailey-finance", "nomina")
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
    temp.replace(path)


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


def report_profile(directory, now, days, rates):
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
    return {"health": health.inspect_runtime(directory, now), "agents": agents, "requestedDays": days,
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


def check_balance(profile, state, now, runner=subprocess.run, home=None):
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
                             "--sdk-root", runtime["openclawPackage"]], env=env, capture_output=True, text=True, timeout=15)
            value = json.loads(output.stdout) if output.returncode == 0 else {"status": "helper_failed"}
            if not isinstance(value, dict) or value.get("status") not in {"ok", "credential_unavailable", "provider_error", "check_failed", "helper_failed", "runtime_unconfigured"}:
                raise ValueError()
        except (OSError, ValueError, subprocess.TimeoutExpired):
            value = {"status": "check_failed"}
    return {**value, "checkedAt": now, "nextCheckAt": now + 3600}


def transition_alerts(previous, current, now):
    before = set(previous)
    after = set(current)
    return ([{"at": now, "state": "opened", "code": code} for code in sorted(after - before)]
            + [{"at": now, "state": "resolved", "code": code} for code in sorted(before - after)])


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
    for profile, detail in report["profiles"].items():
        for agent, value in detail["agents"].items():
            cost = value["estimatedCostPerVerifiedOutcomeUsdRange"]
            rows.append("<tr>" + "".join("<td>" + html.escape(str(item)) + "</td>" for item in (
                profile + "/" + agent, value["completedResponses"], value["failedRuns"], value["verifiedBusinessOutcomes"],
                value["responseP95Seconds"] if value["responseP95Seconds"] is not None else "—", cost if cost else "Unknown")) + "</tr>")
    return """<!doctype html><meta charset="utf-8"><title>Agent operations</title>
<style>body{font:16px system-ui;margin:3rem;max-width:1100px;color:#172330}td,th{padding:.6rem;text-align:left;border-bottom:1px solid #ddd}pre{white-space:pre-wrap}code{background:#eee}</style>
<h1>Agent operations</h1><p>Updated """ + html.escape(report["at"]) + """</p><h2>Active local alerts</h2><pre>""" + html.escape("\n".join(report["activeAlerts"]) or "No active recorded alerts") + """</pre>
<table><tr><th>Agent</th><th>Completed responses</th><th>Failed runs</th><th>Verified outcomes</th><th>P95 seconds</th><th>Estimated USD / outcome</th></tr>""" + "".join(rows) + """</table>
<p>Completed responses require observed assistant text from a successful run; this does not prove channel delivery. Older runs lack this observation. Verified outcomes are explicit tool receipts, not final human approval or delivery. Costs require full known-rate coverage. Unknown is not zero. Human review time is not yet recorded. Retained journals may cover less than the requested period.</p>
<h2>Provider balance checks</h2><pre>""" + html.escape(json.dumps(report["balances"], indent=2)) + "</pre><h2>Encrypted recovery</h2><pre>" + html.escape(json.dumps(report["recovery"], indent=2)) + "</pre>"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", action="append", choices=PROFILES)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--days", type=int, choices=range(1, 31), default=7)
    parser.add_argument("--rates", type=Path, help="Explicit USD per million-token low/high rates; no guessed legacy aliases")
    parser.add_argument("--check-balance", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.output_dir / "monitor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        now = time.time()
        state_path = args.output_dir / "state.json"
        state = health.load_json(state_path, {})
        rates = health.load_json(args.rates, {}) if args.rates else {}
        profiles, balances, alerts = {}, dict(state.get("balances", {})), []
        for profile in args.profile or PROFILES:
            directory = Path.home() / (".openclaw-" + profile)
            profiles[profile] = report_profile(directory, now, args.days, rates)
            alerts.extend(profile + ":" + reason for reason in profiles[profile]["health"]["reasons"])
            if not profiles[profile]["health"]["instrumented"]:
                alerts.append(profile + ":telemetry_missing")
            if args.check_balance:
                balances[profile] = check_balance(profile, state, now)
            balance = balances.get(profile, {})
            if balance:
                if balance.get("status") != "ok":
                    alerts.append(profile + ":balance_unverified")
                elif balance.get("available") is False or any(item["currency"] == "USD" and item["total"] < 5 for item in balance.get("balances", [])):
                    alerts.append(profile + ":provider_balance_low")
        recovery = recovery_status(Path.home() / "Backups/owlswatch-agents/encrypted-runtime", now)
        if recovery["status"] != "fresh":
            alerts.append("recovery:" + recovery["status"])
        elif not set(profiles).issubset(recovery["profiles"]):
            alerts.append("recovery:profile_coverage_incomplete")
        report = {"at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(), "profiles": profiles, "balances": balances,
                  "recovery": recovery, "activeAlerts": sorted(set(alerts))}
        events = transition_alerts(state.get("activeAlerts", []), report["activeAlerts"], now)
        history = health.load_json(args.output_dir / "alerts.json", [])
        atomic_json(args.output_dir / "alerts.json", (history + events)[-2000:])
        atomic_json(args.output_dir / "report.json", report)
        atomic_json(state_path, {"balances": balances, "activeAlerts": report["activeAlerts"]})
        (args.output_dir / "index.html").write_text(html_report(report))
        print(json.dumps({"report": str(args.output_dir / "index.html"), "newTransitions": len(events), "activeAlerts": report["activeAlerts"]}))


if __name__ == "__main__":
    main()
