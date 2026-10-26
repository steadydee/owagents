#!/usr/bin/env python3
"""Observe one OpenClaw Telegram channel without polling or restarting it.

OpenClaw remains the sole Telegram long-poll consumer and recovery owner. This
observer records health/ingress metadata and sends transition-only alerts.
Message bodies and credentials are never written to the observer logs.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


HEALTH_LOG_MAX_LINES = 5000
INGRESS_JOURNAL_MAX_LINES = 10000
SEEN_MESSAGE_LIMIT = 2500
STALE_TRANSPORT_SECONDS = 5 * 60
STALE_SPOOL_SECONDS = 10 * 60
REPEAT_ALERT_SECONDS = 6 * 60 * 60
FAILURE_ALERT_AFTER_SECONDS = 10 * 60
RECOVERY_HEALTHY_SAMPLES = 3
RECOVERY_SAMPLE_MAX_GAP_SECONDS = 5 * 60


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--openclaw-bin", default="openclaw")
    parser.add_argument("--status-json", help="Read a fixture instead of invoking OpenClaw")
    parser.add_argument("--chat-id", help="Telegram chat for transition alerts")
    parser.add_argument("--dry-run", action="store_true", help="Never send Telegram alerts")
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def append_json_line(path: Path, value: Dict[str, Any], max_lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n")
    os.chmod(path, 0o600)
    try:
        if path.stat().st_size > 5 * 1024 * 1024:
            lines = path.read_text(encoding="utf-8").splitlines()[-max_lines:]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.chmod(path, 0o600)
    except OSError:
        pass


def decode_json_output(raw: str) -> Dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("OpenClaw status output did not contain a JSON object")


def get_status(args: argparse.Namespace) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if args.status_json:
        value = load_json(Path(args.status_json), None)
        return (value, None) if isinstance(value, dict) else (None, "invalid_status_fixture")
    try:
        result = subprocess.run(
            [args.openclaw_bin, "--profile", args.profile, "channels", "status", "--probe", "--json"],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, "status_command_failed:" + type(error).__name__
    if result.returncode != 0:
        return None, "status_command_exit:" + str(result.returncode)
    try:
        return decode_json_output(result.stdout), None
    except ValueError:
        return None, "status_json_missing"


def first_telegram_account(status: Dict[str, Any]) -> Dict[str, Any]:
    accounts = status.get("channelAccounts", {}).get("telegram", [])
    if isinstance(accounts, list) and accounts and isinstance(accounts[0], dict):
        return accounts[0]
    return {}


def inspect_spool(state_dir: Path, now: float) -> Dict[str, Any]:
    spool = state_dir / "telegram" / "ingress-spool-default"
    files = [path for path in spool.rglob("*") if path.is_file()] if spool.exists() else []
    oldest_age = max((now - path.stat().st_mtime for path in files), default=0.0)
    return {
        "count": len(files),
        "oldestAgeSeconds": round(max(0.0, oldest_age), 1),
    }


def inspect_offset(state_dir: Path) -> Dict[str, Any]:
    path = state_dir / "telegram" / "update-offset-default.json"
    if not path.exists():
        return {"present": False, "storage": "sqlite" if (state_dir / "state" / "openclaw.sqlite").exists() else None}
    value = load_json(path, {})
    if not isinstance(value, dict):
        return {"present": False}
    return {
        "present": True,
        "lastUpdateId": value.get("lastUpdateId"),
        "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
        if path.exists()
        else None,
    }


def inspect_durable_ingress(state_dir: Path, now_ms: int) -> Dict[str, Any]:
    database = state_dir / "state" / "openclaw.sqlite"
    empty = {"present": False, "pending": 0, "claimed": 0, "recentFailed": 0, "oldestActiveAgeSeconds": 0.0}
    if not database.exists():
        return empty
    try:
        connection = sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True, timeout=5)
        counts = dict(
            connection.execute(
                """
                SELECT status, COUNT(*)
                FROM channel_ingress_events
                WHERE channel_id = 'telegram'
                GROUP BY status
                """
            ).fetchall()
        )
        oldest = connection.execute(
            """
            SELECT MIN(received_at)
            FROM channel_ingress_events
            WHERE channel_id = 'telegram' AND status IN ('pending', 'claimed')
            """
        ).fetchone()[0]
        recent_failed = connection.execute(
            """
            SELECT COUNT(*)
            FROM channel_ingress_events
            WHERE channel_id = 'telegram' AND status = 'failed' AND failed_at >= ?
            """,
            (now_ms - STALE_SPOOL_SECONDS * 1000,),
        ).fetchone()[0]
        connection.close()
    except (sqlite3.Error, OSError, TypeError):
        return {**empty, "present": True, "readError": True}
    oldest_age = max(0.0, (now_ms - oldest) / 1000.0) if isinstance(oldest, (int, float)) else 0.0
    return {
        "present": True,
        "pending": int(counts.get("pending", 0)),
        "claimed": int(counts.get("claimed", 0)),
        "completed": int(counts.get("completed", 0)),
        "failed": int(counts.get("failed", 0)),
        "recentFailed": int(recent_failed or 0),
        "oldestActiveAgeSeconds": round(oldest_age, 1),
    }


def iter_cached_messages(state_dir: Path) -> Iterable[Dict[str, Any]]:
    pattern = str(state_dir / "agents" / "*" / "sessions" / "sessions.json.telegram-messages.json")
    for filename in glob.glob(pattern):
        try:
            lines = Path(filename).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                record = json.loads(line)
                message = record.get("node", {}).get("sourceMessage", {})
            except (json.JSONDecodeError, AttributeError):
                continue
            if not isinstance(message, dict):
                continue
            sender = message.get("from") if isinstance(message.get("from"), dict) else {}
            chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
            signature_input = ":".join(
                str(value)
                for value in (
                    chat.get("id"),
                    message.get("message_id"),
                    sender.get("id"),
                    message.get("date"),
                    record.get("key"),
                )
            )
            yield {
                "signature": hashlib.sha256(signature_input.encode("utf-8")).hexdigest(),
                "messageId": message.get("message_id"),
                "chatId": chat.get("id"),
                "senderId": sender.get("id"),
                "messageDate": message.get("date"),
            }

    database = state_dir / "state" / "openclaw.sqlite"
    if not database.exists():
        return
    try:
        connection = sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True, timeout=5)
        rows = connection.execute(
            """
            SELECT entry_key, value_json
            FROM plugin_state_entries
            WHERE plugin_id = 'telegram'
              AND namespace = 'telegram.message-cache'
            ORDER BY created_at ASC, entry_key ASC
            """
        ).fetchall()
        connection.close()
    except (sqlite3.Error, OSError):
        return
    for entry_key, value_json in rows:
        try:
            value = json.loads(value_json)
            message = value.get("sourceMessage", {})
        except (json.JSONDecodeError, AttributeError):
            continue
        if not isinstance(message, dict):
            continue
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        signature_input = ":".join(
            str(item)
            for item in (chat.get("id"), message.get("message_id"), sender.get("id"), message.get("date"), entry_key)
        )
        yield {
            "signature": hashlib.sha256(signature_input.encode("utf-8")).hexdigest(),
            "messageId": message.get("message_id"),
            "chatId": chat.get("id"),
            "senderId": sender.get("id"),
            "messageDate": message.get("date"),
        }


def journal_new_messages(state_dir: Path, state: Dict[str, Any]) -> int:
    seen = list(state.get("seenMessageSignatures", []))
    seen_set = set(seen)
    added = 0
    journal = state_dir / "logs" / "telegram-ingress-journal.jsonl"
    for message in iter_cached_messages(state_dir):
        signature = message.pop("signature")
        if signature in seen_set:
            continue
        append_json_line(journal, {"observedAt": utc_now(), **message}, INGRESS_JOURNAL_MAX_LINES)
        seen.append(signature)
        seen_set.add(signature)
        added += 1
    state["seenMessageSignatures"] = seen[-SEEN_MESSAGE_LIMIT:]
    return added


def assess_health(
    status: Optional[Dict[str, Any]],
    status_error: Optional[str],
    spool: Dict[str, Any],
    durable_ingress: Dict[str, Any],
    now_ms: int,
) -> Tuple[List[str], Dict[str, Any]]:
    reasons: List[str] = []
    summary: Dict[str, Any] = {}
    if status_error or not status:
        reasons.append(status_error or "status_unavailable")
        return reasons, summary

    channel = status.get("channels", {}).get("telegram", {})
    account = first_telegram_account(status)
    probe = channel.get("probe") if isinstance(channel.get("probe"), dict) else {}

    configured = channel.get("configured") is True
    running = channel.get("running") is True
    connected = account.get("connected") is True
    probe_ok = probe.get("ok") is True
    restart_pending = account.get("restartPending") is True
    last_transport = account.get("lastTransportActivityAt")
    transport_age = None
    if isinstance(last_transport, (int, float)):
        transport_age = max(0.0, (now_ms - last_transport) / 1000.0)

    if not configured:
        reasons.append("telegram_not_configured")
    if not running:
        reasons.append("telegram_not_running")
    if not connected:
        reasons.append("telegram_not_connected")
    if not probe_ok:
        reasons.append("telegram_probe_failed")
    if restart_pending:
        reasons.append("telegram_restart_pending")
    if account.get("lastError"):
        reasons.append("telegram_last_error")
    if transport_age is not None and transport_age > STALE_TRANSPORT_SECONDS:
        reasons.append("telegram_transport_stale")
    if spool["count"] and spool["oldestAgeSeconds"] > STALE_SPOOL_SECONDS:
        reasons.append("ingress_spool_stalled")
    if durable_ingress.get("oldestActiveAgeSeconds", 0) > STALE_SPOOL_SECONDS:
        reasons.append("durable_ingress_stalled")
    if durable_ingress.get("recentFailed", 0):
        reasons.append("durable_ingress_failed")

    summary = {
        "configured": configured,
        "running": running,
        "connected": connected,
        "probeOk": probe_ok,
        "restartPending": restart_pending,
        "reconnectAttempts": account.get("reconnectAttempts"),
        "lastStartAt": account.get("lastStartAt"),
        "lastTransportActivityAt": last_transport,
        "transportAgeSeconds": round(transport_age, 1) if transport_age is not None else None,
        "lastInboundAt": account.get("lastInboundAt"),
        "lastOutboundAt": account.get("lastOutboundAt"),
    }
    return reasons, summary


def resolve_alert_config(state_dir: Path, explicit_chat_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    config = load_json(state_dir / "openclaw.json", {})
    telegram = config.get("channels", {}).get("telegram", {}) if isinstance(config, dict) else {}
    token = telegram.get("botToken") if isinstance(telegram, dict) else None
    chat_id = explicit_chat_id
    if not chat_id and isinstance(telegram, dict):
        groups = telegram.get("groups", {})
        if isinstance(groups, dict) and groups:
            chat_id = next(iter(groups.keys()))
    return (token if isinstance(token, str) else None, str(chat_id) if chat_id else None)


def send_alert(token: Optional[str], chat_id: Optional[str], message: str, dry_run: bool) -> bool:
    if dry_run:
        return True
    if not token or not chat_id:
        return False
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=payload,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            value = json.loads(response.read().decode("utf-8"))
        return value.get("ok") is True
    except Exception:
        return False


def health_transition(state: Dict[str, Any], reasons: List[str], now: float) -> Dict[str, Any]:
    """Debounce recovery without losing the incident during a brief good sample."""
    previous_health = state.get("health", "unknown")
    outage_started_at = float(state.get("outageStartedAtEpoch", 0) or 0)
    outage_alert_sent = state.get("outageAlertSent") is True
    last_alert_at = float(state.get("lastAlertAtEpoch", 0) or 0)
    outage_reasons = state.get("outageReasons", state.get("lastReasons", []))
    outage_reasons = list(outage_reasons) if isinstance(outage_reasons, list) else []
    consecutive_failures = int(state.get("consecutiveFailures", 0))
    healthy_samples = 0
    alert_kind = None
    resolved_reasons = []
    if reasons:
        if not outage_started_at:
            outage_started_at = now
            outage_alert_sent = False
            outage_reasons = []
        outage_reasons = sorted(set(outage_reasons + reasons))
        consecutive_failures += 1
        current_health = "unhealthy" if now - outage_started_at >= FAILURE_ALERT_AFTER_SECONDS else "suspect"
        if current_health == "unhealthy" and not outage_alert_sent and (
            previous_health != "unhealthy" or now - last_alert_at >= REPEAT_ALERT_SECONDS
        ):
            alert_kind = "failure"
    else:
        consecutive_failures = 0
        gap = now - float(state.get("lastCheckedAtEpoch", 0) or 0)
        prior_samples = int(state.get("consecutiveHealthy", 0)) if 0 <= gap <= RECOVERY_SAMPLE_MAX_GAP_SECONDS else 0
        healthy_samples = min(RECOVERY_HEALTHY_SAMPLES, prior_samples + 1)
        if outage_started_at or outage_alert_sent:
            current_health = "recovering" if healthy_samples < RECOVERY_HEALTHY_SAMPLES else "healthy"
            if current_health == "healthy":
                resolved_reasons = outage_reasons
                if outage_alert_sent:
                    alert_kind = "recovery"
                outage_started_at, outage_alert_sent, outage_reasons = 0, False, []
        else:
            current_health = "healthy"
    return {"health": current_health, "consecutiveFailures": consecutive_failures,
            "consecutiveHealthy": healthy_samples, "lastCheckedAtEpoch": now,
            "outageStartedAtEpoch": outage_started_at, "outageAlertSent": outage_alert_sent,
            "outageReasons": outage_reasons, "alertKind": alert_kind, "resolvedReasons": resolved_reasons}


def recovery_evidence(reasons: List[str], runtime: Dict[str, Any]) -> Dict[str, Any]:
    agents = sorted({reason.split(":")[1] for reason in reasons if reason.startswith(("agent_blocked:", "agent_unfinished:"))})
    successes, reviewed = [], []
    for agent in agents:
        last = runtime.get("agents", {}).get(agent, {})
        active = runtime.get("unfinishedRuns", {}).get(agent, {})
        if last.get("operatorDisposition") or active.get("operatorDisposition"):
            reviewed.append(agent)
        elif last.get("success") is True:
            successes.append(agent)
    transport = any(reason.startswith(("telegram_", "status_")) for reason in reasons)
    return {"scope": "mixed" if agents and transport else "runtime" if agents else "transport" if transport else "checks",
            "newSuccessfulRuns": successes, "operatorDispositions": reviewed,
            "businessCompletionVerified": False}


def recovery_message(profile: str, evidence: Dict[str, Any]) -> str:
    lines = ["Estado del bot " + profile]
    if evidence["scope"] in {"transport", "mixed"}:
        lines.append("Conexion de Telegram restablecida en tres controles consecutivos.")
    if evidence["newSuccessfulRuns"]:
        lines.append("Nueva ejecucion exitosa observada: " + ", ".join(evidence["newSuccessfulRuns"]) + ".")
    if evidence["operatorDispositions"]:
        lines.append("Incidencia cerrada por el operador: " + ", ".join(evidence["operatorDispositions"]) + ".")
    if evidence["scope"] == "checks":
        lines.append("Las alertas de los controles se despejaron en tres muestras consecutivas.")
    lines.append("Esto no confirma que las tareas pendientes se hayan completado; verifique su resultado en la aplicacion.")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    state_dir = Path(os.path.expanduser(args.state_dir)).resolve()
    monitor_dir = state_dir / "monitor"
    state_path = monitor_dir / "telegram-health-state.json"
    state = load_json(state_path, {})
    if not isinstance(state, dict):
        state = {}

    now = time.time()
    now_ms = int(now * 1000)
    status, status_error = get_status(args)
    spool = inspect_spool(state_dir, now)
    durable_ingress = inspect_durable_ingress(state_dir, now_ms)
    offset = inspect_offset(state_dir)
    journaled = journal_new_messages(state_dir, state)
    reasons, summary = assess_health(status, status_error, spool, durable_ingress, now_ms)
    transport_reasons = list(reasons)
    runtime = {}
    health_module = Path(__file__).with_name("agent-health.py")
    if health_module.exists():
        try:
            spec = importlib.util.spec_from_file_location("agent_runtime_health", health_module)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            runtime = module.inspect_runtime(state_dir, now, previous=state.get("runtimeEvidence"))
            reasons.extend(runtime.get("reasons", []))
            state["runtimeEvidence"] = {key: runtime[key] for key in ("agents", "unfinishedRuns")}
        except Exception:
            reasons.append("runtime_health_unavailable")
    else:
        reasons.append("runtime_health_unavailable")

    transition = health_transition(state, reasons, now)
    alert_kind = transition.pop("alertKind")
    recovery = recovery_evidence(transition.pop("resolvedReasons"), runtime) if alert_kind == "recovery" else None
    state.update(transition)
    current_health = state["health"]

    token, chat_id = resolve_alert_config(state_dir, args.chat_id)
    alert_sent = None
    if alert_kind == "failure":
        label = ", ".join(reasons[:3])
        alert_sent = send_alert(
            token,
            chat_id,
            "Alerta del bot " + args.profile + "\nEl servicio necesita revision ("
            + label
            + "). Revise el canal, el saldo del proveedor o el estado de la tarea segun el motivo.",
            args.dry_run,
        )
    elif alert_kind == "recovery":
        alert_sent = send_alert(
            token,
            chat_id,
            recovery_message(args.profile, recovery),
            args.dry_run,
        )

    if alert_kind == "failure" and alert_sent:
        state["outageAlertSent"] = True
        state["lastAlertAtEpoch"] = now
    elif alert_kind == "recovery" and alert_sent:
        state["lastAlertAtEpoch"] = now

    snapshot = {
        "observedAt": utc_now(),
        "profile": args.profile,
        "health": current_health,
        "reasons": reasons,
        "channel": summary,
        "channelChecks": {"health": "unhealthy" if transport_reasons else "healthy", "reasons": transport_reasons},
        "runtime": runtime,
        "recoveryEvidence": recovery,
        "consecutiveHealthy": state["consecutiveHealthy"],
        "spool": spool,
        "durableIngress": durable_ingress,
        "offset": offset,
        "journaledMessages": journaled,
        "alertKind": alert_kind,
        "alertSent": alert_sent,
    }
    append_json_line(state_dir / "logs" / "telegram-health.jsonl", snapshot, HEALTH_LOG_MAX_LINES)

    state.update(
        {
            "health": current_health,
            "lastCheckedAt": snapshot["observedAt"],
            "lastReasons": reasons,
        }
    )
    atomic_write_json(state_path, state)
    print(json.dumps(snapshot, ensure_ascii=True, sort_keys=True))
    return 0 if current_health != "unhealthy" else 1


if __name__ == "__main__":
    sys.exit(main())
