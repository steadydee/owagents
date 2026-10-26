#!/usr/bin/env python3
"""Read content-free runtime telemetry; no model/provider calls or messages."""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


CODES = {"provider_credit", "context_limit", "provider_timeout", "transport", "provider_rate_limit", "provider_auth", "run_failed", "run_budget"}
DISPOSITIONS = {"resolved_externally", "closed_without_retry"}


def count(value):
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number >= 0 else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


def safe_agent(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-z0-9_-]{1,80}", value) else "unknown"


def correlation(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{24}", value) else None


def evidence_id(agent, at, code, run_key):
    return hashlib.sha256(json.dumps([agent, at, code, run_key]).encode()).hexdigest()[:24]


def operator_dispositions(directory):
    path = directory / "health/run-dispositions.json"
    try:
        info = path.stat()
        if path.is_symlink() or not path.is_file() or info.st_mode & 0o077 or info.st_uid not in (0, os.getuid()):
            return {}
    except OSError:
        return {}
    return load_json(path, {})


def disposition_for(dispositions, agent, evidence, now):
    """Only an operator's exact incident disposition can close failed evidence."""
    if not isinstance(dispositions, dict) or dispositions.get("schemaVersion") != 1:
        return None
    entries = dispositions.get("dispositions", [])
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        at = timestamp(entry.get("recordedAt"))
        if (entry.get("agent") == agent and entry.get("evidenceId") == evidence["evidenceId"]
                and isinstance(entry.get("resolution"), str) and entry["resolution"] in DISPOSITIONS and at is not None
                and timestamp(evidence["at"]) <= at <= now + 60):
            return {"resolution": entry["resolution"], "recordedAt": entry["recordedAt"]}
    return None


def inspect_runtime(directory, now=None, previous=None):
    now = time.time() if now is None else now
    latest, latest_times, runs, starts = {}, {}, {}, {}
    observed = False
    legacy_incidents = {}
    if previous is None:
        state = load_json(directory / "monitor/telegram-health-state.json", {})
        previous = state.get("runtimeEvidence", {}) if isinstance(state, dict) else {}
        # Upgrade older observers without treating already-rotated incident
        # evidence as recovery. The retained incident can be reviewed explicitly.
        if isinstance(state, dict) and not previous:
            missing = {}
            incident_at = count(state.get("outageStartedAtEpoch")) or timestamp(state.get("lastCheckedAt")) or now
            incident_reasons = []
            for field in ("lastReasons", "outageReasons"):
                if isinstance(state.get(field), list):
                    incident_reasons.extend(state[field])
            for reason in incident_reasons:
                if not isinstance(reason, str) or not reason.startswith(("agent_blocked:", "agent_unfinished:")):
                    continue
                parts = reason.split(":")
                if len(parts) < 2 or safe_agent(parts[1]) != parts[1]:
                    continue
                missing[parts[1]] = {"at": dt.datetime.fromtimestamp(incident_at, dt.timezone.utc).isoformat(),
                                     "success": False, "code": parts[2] if len(parts) > 2 and parts[2] in CODES else "run_failed"}
            legacy_incidents = missing
    previous = previous if isinstance(previous, dict) else {}
    # Preserve content-free evidence after telemetry rotation. A missing log or
    # an elapsed reporting window is not a successful retry.
    cached_agents = previous.get("agents", {})
    for agent, last in (cached_agents.items() if isinstance(cached_agents, dict) else []):
        if safe_agent(agent) != agent or not isinstance(last, dict):
            continue
        ended = timestamp(last.get("at"))
        observed_at = timestamp(last.get("observedAt")) or ended
        if ended is None or ended > now + 60 or observed_at > now + 60:
            continue
        success = last.get("success") is True and not last.get("code")
        latest[agent] = {"at": last["at"], "success": success,
                         "code": None if success else (last.get("code") if isinstance(last.get("code"), str) and last["code"] in CODES else "run_failed"),
                         "runKey": correlation(last.get("runKey")),
                         "observedAt": dt.datetime.fromtimestamp(observed_at, dt.timezone.utc).isoformat(),
                         "revision": count(last.get("revision"))}
        latest[agent].update({field: count(last.get(field)) for field in
                             ("durationMs", "calls", "searches", "tokens", "modelRequests", "modelErrors")})
        latest_times[agent] = (ended, (observed_at, count(last.get("revision"))))
    cached_starts = previous.get("unfinishedRuns", {})
    for agent, active in (cached_starts.items() if isinstance(cached_starts, dict) else []):
        if safe_agent(agent) != agent or not isinstance(active, dict):
            continue
        at, started = timestamp(active.get("observedAt")), timestamp(active.get("startedAt"))
        key = correlation(active.get("runKey"))
        if key and at is not None and started is not None and started <= at <= now + 60:
            starts[(key, at)] = (at, started, agent)
    seen_calls, seen_usage = set(), set()
    totals = {"modelRequests": 0, "modelErrors": 0, "tokens": 0, "attemptsWithUsage": 0, "failedRuns": 0, "completedRuns": 0}
    for path in [directory / "health/runs.jsonl.previous", directory / "health/runs.jsonl"]:
        try:
            stream = path.open(errors="replace")
        except OSError:
            continue
        with stream:
            for lineno, line in enumerate(stream):
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(item, dict):
                    continue
                at = timestamp(item.get("at"))
                if at is None or at > now + 60:
                    continue
                # Legacy snapshots have no correlation key and cannot be revised.
                # New snapshots replace the same run, even when llm_output arrives
                # after agent_end and changes a provisional success into an error.
                run_key = correlation(item.get("runKey"))
                identity = run_key or (path.name, lineno)
                kind = item.get("kind")
                if not isinstance(kind, str):
                    continue
                observed = observed or kind in {"run_start", "run_end", "model_call", "usage", "budget_stop"}
                if kind == "run_start" and run_key:
                    started = timestamp(item.get("startedAt"))
                    if started is None or started > at:
                        started = at
                    starts[(run_key, at)] = (at, started, safe_agent(item.get("agent")))
                if kind == "run_end":
                    ended = timestamp(item.get("endedAt")) or at
                    if ended > now + 60:
                        continue
                    revision = count(item.get("revision"))
                    ordering = (at, revision)
                    if identity in runs and ordering < runs[identity][0]:
                        continue
                    code = item.get("code")
                    success = item.get("success") is True and not code
                    safe = {"at": dt.datetime.fromtimestamp(ended, dt.timezone.utc).isoformat(), "success": success,
                            "code": None if success else (code if isinstance(code, str) and code in CODES else "run_failed"),
                            "runKey": run_key, "observedAt": item["at"], "revision": revision}
                    safe.update({field: count(item.get(field)) for field in ("durationMs", "calls", "searches", "tokens", "modelRequests", "modelErrors")})
                    runs[identity] = (ordering, ended, safe_agent(item.get("agent")), safe)
                if now - at > 86400:
                    continue
                if kind == "model_call":
                    call_key = correlation(item.get("callKey"))
                    call_identity = (identity, call_key) if call_key else (path.name, lineno)
                    if call_identity in seen_calls:
                        continue
                    seen_calls.add(call_identity)
                    totals["modelRequests"] += 1
                    totals["modelErrors"] += item.get("success") is False
                elif kind == "usage":
                    usage_key = correlation(item.get("usageKey"))
                    usage_identity = (identity, usage_key) if usage_key else (path.name, lineno)
                    if usage_identity in seen_usage:
                        continue
                    seen_usage.add(usage_identity)
                    totals["attemptsWithUsage"] += 1
                    totals["tokens"] += count(item.get("tokens"))
    for ordering, ended, agent, safe in runs.values():
        if (ended, ordering) >= latest_times.get(agent, (float("-inf"), (0, 0))):
            latest[agent] = safe
            latest_times[agent] = (ended, ordering)
        if now - ended <= 86400:
            totals["completedRuns" if safe["success"] else "failedRuns"] += 1
    for agent, incident in legacy_incidents.items():
        at = timestamp(incident["at"])
        # Prefer real failed evidence. An older success cannot close an
        # incident whose failed telemetry has already rotated away.
        last = latest.get(agent)
        if last is None or (last["success"] and latest_times[agent][1][0] < at):
            latest[agent] = {**incident, "runKey": None, "observedAt": incident["at"], "revision": 0}
            latest_times[agent] = (at, (at, 0))
    circuit = load_json(directory / "health/provider-circuit.json", {})
    reasons = []
    if isinstance(circuit, dict) and count(circuit.get("until")) / 1000 > now:
        reasons.append("provider_credit")
    dispositions = operator_dispositions(directory)
    for agent, last in latest.items():
        last["evidenceAgeSeconds"] = max(0, int(now - latest_times[agent][0]))
        last["staleEvidence"] = last["evidenceAgeSeconds"] > 86400
        last["evidenceState"] = "successful_run" if last["success"] else "unresolved_failure"
        if last["success"] is False:
            last["evidenceId"] = evidence_id(agent, last["at"], last["code"], last["runKey"])
            last["operatorDisposition"] = disposition_for(dispositions, agent, last, now)
            if last["operatorDisposition"]:
                last["evidenceState"] = "operator_disposition"
            else:
                reasons.append(f"agent_blocked:{agent}:{last.get('code') or 'run_failed'}")
    unfinished = {}
    for (run_key, _), (at, started, agent) in sorted(starts.items(), key=lambda item: item[1][0]):
        # A new start is not recovery evidence. Keep the earliest unresolved
        # attempt until an end arrives or an operator disposes of that incident.
        if (run_key in runs and runs[run_key][1] >= at) or latest_times.get(agent, (float("-inf"),))[0] >= at:
            continue
        age = max(0, int(now - started))
        active = {"startedAt": dt.datetime.fromtimestamp(started, dt.timezone.utc).isoformat(),
                             "observedAt": dt.datetime.fromtimestamp(at, dt.timezone.utc).isoformat(),
                             "runKey": run_key, "currentRunAgeSeconds": age, "staleRunning": age > 900,
                             "staleEvidence": now - at > 86400,
                             "evidenceId": evidence_id(agent, str(at), "unfinished", run_key)}
        active["operatorDisposition"] = disposition_for(dispositions, agent, {**active, "at": active["observedAt"]}, now)
        if agent not in unfinished or (unfinished[agent]["operatorDisposition"] and not active["operatorDisposition"]):
            unfinished[agent] = active
    for agent, active in unfinished.items():
        if active["staleRunning"]:
            if not active["operatorDisposition"]:
                reasons.append(f"agent_unfinished:{agent}")
    return {"instrumented": observed, "agents": latest, "unfinishedRuns": unfinished, "last24Hours": totals, "reasons": reasons}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["owlswatch", "hotel", "finca", "bailey-finance"], required=True)
    args = parser.parse_args()
    print(json.dumps({"profile": args.profile, **inspect_runtime(Path.home() / (".openclaw-" + args.profile))}, indent=2))
