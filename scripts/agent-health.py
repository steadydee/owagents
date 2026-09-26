#!/usr/bin/env python3
"""Read content-free runtime telemetry; no model/provider calls or messages."""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import re
import time


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


CODES = {"provider_credit", "context_limit", "provider_timeout", "transport", "provider_rate_limit", "provider_auth", "run_failed", "run_budget"}


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


def inspect_runtime(directory, now=None):
    now = time.time() if now is None else now
    latest, latest_times, runs, starts = {}, {}, {}, {}
    observed = False
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
                    starts[run_key] = (at, started, safe_agent(item.get("agent")))
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
                            "code": None if success else (code if isinstance(code, str) and code in CODES else "run_failed")}
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
    circuit = load_json(directory / "health/provider-circuit.json", {})
    reasons = []
    if isinstance(circuit, dict) and count(circuit.get("until")) / 1000 > now:
        reasons.append("provider_credit")
    for agent, last in latest.items():
        # Old failures remain visible as history but do not keep a healthy
        # gateway's observer in an outage state indefinitely.
        if last["success"] is False and now - latest_times[agent][0] <= 86400:
            reasons.append(f"agent_blocked:{agent}:{last.get('code') or 'run_failed'}")
    unfinished, latest_starts = {}, {}
    for run_key, (at, started, agent) in starts.items():
        if now - at > 86400 or at < latest_starts.get(agent, float("-inf")):
            continue
        latest_starts[agent] = at
        unfinished.pop(agent, None)
        # A later completed/new run supersedes an abandoned run. A new attempt
        # with the same ID can follow a previous end and is still unfinished.
        if (run_key in runs and runs[run_key][1] >= at) or latest_times.get(agent, (float("-inf"),))[0] >= at:
            continue
        age = max(0, int(now - started))
        unfinished[agent] = {"startedAt": dt.datetime.fromtimestamp(started, dt.timezone.utc).isoformat(),
                             "currentRunAgeSeconds": age, "staleRunning": age > 900}
    for agent, active in unfinished.items():
        if active["staleRunning"]:
            reasons.append(f"agent_unfinished:{agent}")
    return {"instrumented": observed, "agents": latest, "unfinishedRuns": unfinished, "last24Hours": totals, "reasons": reasons}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["owlswatch", "hotel", "finca", "bailey-finance"], required=True)
    args = parser.parse_args()
    print(json.dumps({"profile": args.profile, **inspect_runtime(Path.home() / (".openclaw-" + args.profile))}, indent=2))
