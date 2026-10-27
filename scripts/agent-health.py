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
EVIDENCE_FRESH_SECONDS = 300


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


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat()


def normalize_outcome(entry, now):
    """Allowlist cached evidence; never echo journal/user content."""
    if not isinstance(entry, dict) or safe_agent(entry.get("agent")) != entry.get("agent"):
        return None
    at, observed = timestamp(entry.get("at")), timestamp(entry.get("observedAt"))
    kind, key = entry.get("kind"), correlation(entry.get("evidenceId"))
    if at is None or observed is None or max(at, observed) > now + 60 or observed < at or not key or not isinstance(kind, str) or kind not in {"failed_run", "unfinished_run"}:
        return None
    safe = {"evidenceId": key, "agent": entry["agent"], "kind": kind, "at": iso(at), "observedAt": iso(observed),
            "runKey": correlation(entry.get("runKey")), "revision": count(entry.get("revision")),
            "code": entry.get("code") if isinstance(entry.get("code"), str) and entry["code"] in CODES else None,
            "state": "pending_review"}
    if kind == "unfinished_run":
        started = timestamp(entry.get("startedAt"))
        if started is None or started > at or not safe["runKey"]:
            return None
        safe["startedAt"] = iso(started)
    elif safe["code"] is None:
        safe["code"] = "run_failed"
    resolution = entry.get("resolution")
    if isinstance(resolution, dict):
        resolved = timestamp(resolution.get("at"))
        if resolved is not None and at <= resolved <= now + 60:
            rkind = resolution.get("kind")
            if rkind == "operator_disposition" and isinstance(resolution.get("resolution"), str) and resolution["resolution"] in DISPOSITIONS:
                safe["resolution"] = {"kind": rkind, "at": iso(resolved), "resolution": resolution["resolution"]}
            elif (observed <= resolved and safe["runKey"] and resolution.get("runKey") == safe["runKey"]
                  and ((rkind == "same_run_response" and kind == "failed_run" and count(resolution.get("revision")) > safe["revision"])
                       or (rkind == "run_ended" and kind == "unfinished_run"))):
                safe["resolution"] = {"kind": rkind, "at": iso(resolved), "runKey": safe["runKey"],
                                      "revision": count(resolution.get("revision"))}
    return safe


def validate_outcome_evidence(entries, now=None):
    """Strict persisted-ledger validation; False must not reset/overwrite state.

    Unlike journal ingestion, a retained ledger cannot silently drop malformed
    rows. Callers should preserve the file and report persistence unavailable.
    """
    now = time.time() if now is None else now
    if not isinstance(entries, list):
        return False
    seen = set()
    for entry in entries:
        safe = normalize_outcome(entry, now)
        if safe is None or type(entry.get("revision")) is not int or entry["revision"] < 0:
            return False
        state = entry.get("state")
        if state not in ("pending_review", "in_progress", "resolved"):
            return False
        if (state == "resolved") != ("resolution" in safe) or (state == "in_progress" and safe["kind"] != "unfinished_run"):
            return False
        expected = evidence_id(safe["agent"], str(timestamp(safe["at"])) if safe["kind"] == "unfinished_run" else safe["at"],
                               "unfinished" if safe["kind"] == "unfinished_run" else safe["code"], safe["runKey"])
        if safe["evidenceId"] != expected or expected in seen:
            return False
        seen.add(expected)
        safe["state"] = state
        if safe != entry:
            return False
    return True


def remember_failure(outcomes, agent, last):
    key = evidence_id(agent, last["at"], last["code"], last["runKey"])
    old = outcomes.get(key)
    ordering = (timestamp(last["observedAt"]), last["revision"])
    if old and ordering <= (timestamp(old["observedAt"]), old["revision"]):
        return
    outcomes[key] = {"evidenceId": key, "agent": agent, "kind": "failed_run",
                     **{name: last[name] for name in ("at", "observedAt", "runKey", "revision", "code")},
                     "state": "pending_review"}


def inspect_runtime(directory, now=None, previous=None):
    now = time.time() if now is None else now
    latest, latest_times, runs, starts, outcomes = {}, {}, {}, {}, {}
    observed = False
    legacy_incidents = {}
    if previous is None:
        state_path = directory / "monitor/telegram-health-state.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if not isinstance(state, dict):
            raise ValueError("retained_observer_state_invalid")
        previous = state.get("runtimeEvidence", {})
        if not isinstance(previous, dict):
            raise ValueError("retained_runtime_evidence_invalid")
        # Upgrade older observers without treating already-rotated incident
        # evidence as recovery. The retained incident can be reviewed explicitly.
        if isinstance(state, dict) and not previous:
            missing = {}
            incident_at = count(state.get("outageStartedAtEpoch")) or timestamp(state.get("lastCheckedAt")) or now
            incident_reasons = []
            for field in ("lastReasons", "outageReasons", "legacyRuntimeReasons"):
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
    if not isinstance(previous, dict):
        raise ValueError("retained_runtime_evidence_invalid")
    if "outcomeEvidence" in previous and not validate_outcome_evidence(previous["outcomeEvidence"], now):
        raise ValueError("retained_outcome_evidence_invalid")
    # Retain incident identities independently of each agent's latest run. This
    # function is read-only: the observer/report owner persists this returned
    # evidence under its own private state lock.
    for entry in previous.get("outcomeEvidence", []) if isinstance(previous.get("outcomeEvidence"), list) else []:
        safe = normalize_outcome(entry, now)
        if safe:
            outcomes[safe["evidenceId"]] = safe
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
        latest[agent] = {"at": iso(ended), "success": success,
                         "code": None if success else (last.get("code") if isinstance(last.get("code"), str) and last["code"] in CODES else "run_failed"),
                         "runKey": correlation(last.get("runKey")),
                         "observedAt": dt.datetime.fromtimestamp(observed_at, dt.timezone.utc).isoformat(),
                         "revision": count(last.get("revision")), "responseCompleted": last.get("responseCompleted") is True}
        latest[agent].update({field: count(last.get(field)) for field in
                             ("durationMs", "calls", "searches", "tokens", "modelRequests", "modelErrors")})
        latest_times[agent] = (ended, (observed_at, count(last.get("revision"))))
        if not success:
            remember_failure(outcomes, agent, latest[agent])
    cached_starts = previous.get("unfinishedRuns", {})
    for agent, active in (cached_starts.items() if isinstance(cached_starts, dict) else []):
        if safe_agent(agent) != agent or not isinstance(active, dict):
            continue
        at, started = timestamp(active.get("observedAt")), timestamp(active.get("startedAt"))
        key = correlation(active.get("runKey"))
        if key and at is not None and started is not None and started <= at <= now + 60:
            starts[(agent, key, at)] = (at, started, agent)
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
                    starts[(safe_agent(item.get("agent")), run_key, at)] = (at, started, safe_agent(item.get("agent")))
                if kind == "run_end":
                    ended = timestamp(item.get("endedAt")) or at
                    if ended > now + 60:
                        continue
                    revision = count(item.get("revision"))
                    observed_at = max(at, ended)
                    ordering = (observed_at, revision)
                    run_identity = (safe_agent(item.get("agent")), identity)
                    code = item.get("code")
                    success = item.get("success") is True and not code
                    safe = {"at": dt.datetime.fromtimestamp(ended, dt.timezone.utc).isoformat(), "success": success,
                            "code": None if success else (code if isinstance(code, str) and code in CODES else "run_failed"),
                            "runKey": run_key, "observedAt": iso(observed_at), "revision": revision,
                            "responseCompleted": item.get("responseCompleted") is True}
                    safe.update({field: count(item.get(field)) for field in ("durationMs", "calls", "searches", "tokens", "modelRequests", "modelErrors")})
                    agent = safe_agent(item.get("agent"))
                    if run_identity not in runs or ordering >= runs[run_identity][0]:
                        runs[run_identity] = (ordering, ended, agent, safe)
                    if not success:
                        remember_failure(outcomes, agent, safe)
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
        # The old combined alert may duplicate a retained real failure. Keep
        # its synthetic incident only when correlated evidence is unavailable.
        if any(entry["agent"] == agent and entry["kind"] == "failed_run" for entry in outcomes.values()):
            continue
        safe = {**incident, "runKey": None, "observedAt": incident["at"], "revision": 0, "responseCompleted": False}
        remember_failure(outcomes, agent, safe)
        at = timestamp(incident["at"])
        if at >= latest_times.get(agent, (float("-inf"),))[0]:
            latest[agent], latest_times[agent] = safe, (at, (at, 0))

    # Starts are independently retained, including multiple unfinished requests
    # for one agent. An unrelated newer ending is never completion evidence.
    for (_, run_key, _), (at, started, agent) in starts.items():
        key = evidence_id(agent, str(at), "unfinished", run_key)
        if key not in outcomes:
            final = runs.get((agent, run_key))
            if final and final[1] >= at:
                # Normal completed runs need no incident ledger row. Failures
                # already have their own evidence; retain only starts that
                # were previously pending or still lack a matching ending.
                continue
            outcomes[key] = {"evidenceId": key, "agent": agent, "kind": "unfinished_run",
                             "at": iso(at), "observedAt": iso(at), "startedAt": iso(started),
                             "runKey": run_key, "revision": 0, "code": None, "state": "pending_review"}
    dispositions = operator_dispositions(directory)
    for entry in outcomes.values():
        last = runs.get((entry["agent"], entry["runKey"])) if entry["runKey"] else None
        if last:
            ordering, ended, _, safe = last
            after = ended >= timestamp(entry["at"]) and ordering >= (timestamp(entry["observedAt"]), entry["revision"])
            if after and entry["kind"] == "unfinished_run":
                entry["resolution"] = {"kind": "run_ended", "at": safe["observedAt"],
                                       "runKey": safe["runKey"], "revision": safe["revision"]}
            elif (after and safe["success"] and safe["responseCompleted"]
                  and safe["revision"] > entry["revision"]):
                entry["resolution"] = {"kind": "same_run_response", "at": safe["observedAt"],
                                       "runKey": safe["runKey"], "revision": safe["revision"]}
        disposition = disposition_for(dispositions, entry["agent"], entry, now)
        if disposition:
            entry["resolution"] = {"kind": "operator_disposition", "at": iso(timestamp(disposition["recordedAt"])),
                                   "resolution": disposition["resolution"]}
        if entry.get("resolution"):
            entry["state"] = "resolved"
        elif entry["kind"] == "unfinished_run" and now - timestamp(entry["startedAt"]) <= 900:
            entry["state"] = "in_progress"
        else:
            entry["state"] = "pending_review"

    reasons, outcome_reasons, unfinished, availability = [], [], {}, {}
    circuit = load_json(directory / "health/provider-circuit.json", {})
    if isinstance(circuit, dict) and count(circuit.get("until")) / 1000 > now:
        reasons.append("provider_credit")
    for agent, last in latest.items():
        age = max(0, int(now - latest_times[agent][0]))
        last.update(evidenceAgeSeconds=age, staleEvidence=age > 86400,
                    evidenceState="successful_run" if last["success"] else "failed_run")
        availability[agent] = {"state": "unknown" if age > EVIDENCE_FRESH_SECONDS else
                               "response_observed" if last["success"] and last["responseCompleted"] else
                               "failure_observed" if not last["success"] else "unknown",
                               "observedAt": last["at"], "validUntil": iso(latest_times[agent][0] + EVIDENCE_FRESH_SECONDS),
                               "evidenceAgeSeconds": age, "businessCompletionVerified": False, "deliveryVerified": False}
        if not last["success"]:
            key = evidence_id(agent, last["at"], last["code"], last["runKey"])
            entry = outcomes[key]
            last["evidenceId"] = key
            last["evidenceState"] = entry["state"]
            last["operatorDisposition"] = disposition_for(dispositions, agent, entry, now)
    for entry in sorted(outcomes.values(), key=lambda e: (e["at"], e["evidenceId"])):
        agent = entry["agent"]
        if entry["state"] == "pending_review":
            outcome_reasons.append("task_pending_review:" + agent)
            reasons.append(f"agent_blocked:{agent}:{entry['code']}" if entry["kind"] == "failed_run" else f"agent_unfinished:{agent}")
        if entry["kind"] == "unfinished_run" and entry["state"] != "resolved" and agent not in unfinished:
            age = max(0, int(now - timestamp(entry["startedAt"])))
            unfinished[agent] = {key: entry[key] for key in ("startedAt", "observedAt", "runKey", "evidenceId")}
            unfinished[agent].update(currentRunAgeSeconds=age, staleRunning=age > 900,
                                    staleEvidence=now - timestamp(entry["at"]) > 86400, operatorDisposition=None)
    evidence = sorted(outcomes.values(), key=lambda entry: (entry["at"], entry["evidenceId"]))
    pending = sum(entry["state"] == "pending_review" for entry in evidence)
    if not validate_outcome_evidence(evidence, now):
        raise ValueError("generated_outcome_evidence_invalid")
    return {"instrumented": observed or bool(evidence), "agents": latest, "unfinishedRuns": unfinished,
            "last24Hours": totals, "reasons": sorted(set(reasons)), "availability": availability,
            "outcomeEvidence": evidence, "outcomeReasons": sorted(set(outcome_reasons)),
            "taskHealth": {"state": "pending_review" if pending else "unknown", "pendingReviewCount": pending,
                           "inProgressCount": sum(entry["state"] == "in_progress" for entry in evidence),
                           "resolvedEvidenceCount": sum(entry["state"] == "resolved" for entry in evidence),
                           "coverage": "retained_runtime_evidence", "businessCompletionVerified": False, "deliveryVerified": False}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["owlswatch", "hotel", "bailey-finance", "nomina"], required=True)
    args = parser.parse_args()
    try:
        result = inspect_runtime(Path.home() / (".openclaw-" + args.profile))
    except (OSError, ValueError):
        result = {"availability": {}, "taskHealth": {"state": "unknown", "evidenceUnavailable": True},
                  "reasons": ["runtime_evidence_unavailable"]}
    print(json.dumps({"profile": args.profile, **result}, indent=2))
