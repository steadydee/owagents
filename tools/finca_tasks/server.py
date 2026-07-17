#!/usr/bin/env python3
"""Narrow OpenClaw tools for OW Finca task tracking."""

from __future__ import annotations

import base64
import datetime as dt
import fcntl
import hashlib
import hmac
import json
import mimetypes
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable


WORKSPACE = Path(os.environ.get("FINCA_WORKSPACE", "~/.openclaw/workspace-finca-ops")).expanduser()
CONFIG_PATH = Path(os.environ.get("OPENCLAW_CONFIG_PATH", "~/.openclaw-finca/openclaw.json")).expanduser()
STATE_DIR = Path(os.environ.get("OPENCLAW_STATE_DIR", "~/.openclaw-finca")).expanduser()
SPOOL_ROOT = WORKSPACE / "spool" / "finca"
ALBUM_ROOT = WORKSPACE / "spool" / "media-groups"
INBOUND_MEDIA_ROOT = STATE_DIR / "media" / "inbound"
MOCK_FILE = Path(os.environ.get("FINCA_TASKS_MOCK_FILE", str(WORKSPACE / "mock" / "finca-tasks.json"))).expanduser()

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,240}$")
TASK_CODE_RE = re.compile(r"^F-\d{1,10}$", re.IGNORECASE)
WORKER_REPORT_CODE_RE = re.compile(
    r"(?m)^(\s*(?:[\u2022*\-]\s*)?)F-\d{1,10}\s*[\u00b7\-:]\s*",
    re.IGNORECASE,
)
ALLOWED_ACTIONS = {"start", "progress", "block", "complete", "assign", "priority", "cancel", "reopen", "note"}
ALLOWED_STATUSES = {"open", "in_progress", "blocked", "completed", "cancelled"}
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 10
MAX_ESTIMATED_MINUTES = 7 * 24 * 60
DEFAULT_OPERATIONS_URL = "https://operations.owlswatch.com"
OPERATIONS_TOOLS = [
    "operations.finca.list_tasks",
    "operations.finca.get_task",
    "operations.finca.list_workers",
    "operations.finca.create_task",
    "operations.finca.update_task",
    "operations.finca.daily_report",
    "operations.finca.attach_photo",
]


class ToolError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def result_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ToolError):
        return {"ok": False, "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable}}
    return {
        "ok": False,
        "error": {
            "code": "internal_error",
            "message": "The Finca tool failed without exposing sensitive details.",
            "retryable": False,
        },
    }


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_config() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ToolError("config_invalid", f"OpenClaw config is invalid JSON at line {exc.lineno}.") from exc


def cfg_env(config: dict[str, Any], key: str) -> str | None:
    direct = os.environ.get(key)
    if direct:
        return direct
    server_env = config.get("mcp", {}).get("servers", {}).get("finca_tasks", {}).get("env", {})
    value = server_env.get(key)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    env_vars = config.get("env", {}).get("vars", {})
    value = env_vars.get(key)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    return None


def read_secret(config: dict[str, Any], value_key: str, file_key: str) -> str:
    value = cfg_env(config, value_key)
    if value:
        return value.strip()
    file_value = cfg_env(config, file_key)
    if file_value:
        path = Path(file_value).expanduser().resolve()
        try:
            secret = path.read_text().strip()
        except OSError as exc:
            raise ToolError("config_missing", f"The configured {value_key} secret file could not be read.") from exc
        if secret:
            return secret
    raise ToolError("config_missing", f"The {value_key} runtime secret is not configured.")


def mock_enabled(config: dict[str, Any]) -> bool:
    return (cfg_env(config, "FINCA_TASKS_MOCKS") or "0").strip() == "1"


def operations_base_url(config: dict[str, Any]) -> str:
    raw = (cfg_env(config, "OPERATIONS_BASE_URL") or DEFAULT_OPERATIONS_URL).rstrip("/")
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("config_invalid", "Operations base URL must be HTTPS.")
    return raw


def property_id(config: dict[str, Any]) -> str:
    value = cfg_env(config, "OPERATIONS_PROPERTY_ID") or "owlswatch"
    return safe_id("propertyId", value)


def telegram_token(config: dict[str, Any]) -> str:
    value = cfg_env(config, "FINCA_TELEGRAM_BOT_TOKEN")
    if value:
        return value
    configured = config.get("channels", {}).get("telegram", {}).get("botToken")
    if isinstance(configured, str) and configured and not configured.startswith("<"):
        return configured
    raise ToolError("config_missing", "The Finca Telegram bot token is not configured.")


def notify_chat_id(config: dict[str, Any]) -> str:
    return safe_id("notifyChatId", cfg_env(config, "FINCA_TELEGRAM_NOTIFY_CHAT_ID"))


def safe_id(name: str, value: Any, required: bool = True) -> str | None:
    if value is None or value == "":
        if required:
            raise ToolError("invalid_input", f"{name} is required.")
        return None
    text = str(value).strip()
    if not SAFE_ID_RE.fullmatch(text):
        raise ToolError("invalid_input", f"{name} is malformed.")
    return text


def safe_text(name: str, value: Any, max_length: int, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ToolError("invalid_input", f"{name} is required.")
        return None
    if not isinstance(value, str):
        raise ToolError("invalid_input", f"{name} must be text.")
    text = value.strip()
    if not text and required:
        raise ToolError("invalid_input", f"{name} is required.")
    return text[:max_length] if text else None


def safe_bool(name: str, value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if value in (1, "1", "true", "yes"):
        return True
    if value in (0, "0", "false", "no"):
        return False
    raise ToolError("invalid_input", f"{name} must be true or false.")


def safe_optional_int(name: str, value: Any, minimum: int, maximum: int) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError("invalid_input", f"{name} must be a whole number.")
    if value < minimum or value > maximum:
        raise ToolError("invalid_input", f"{name} must be from {minimum} to {maximum}.")
    return value


def safe_task_code(value: Any) -> str:
    code = safe_text("taskCode", value, 20, required=True).upper()
    if not TASK_CODE_RE.fullmatch(code):
        raise ToolError("invalid_input", "taskCode must look like F-0042.")
    return code


def normalize_actor(value: Any) -> dict[str, str | None]:
    raw = value if isinstance(value, dict) else {}
    return {
        "telegramChatId": safe_id("telegramChatId", raw.get("telegramChatId"), required=False),
        "telegramUserId": safe_id("telegramUserId", raw.get("telegramUserId"), required=False),
        "telegramMessageId": safe_id("telegramMessageId", raw.get("telegramMessageId"), required=False),
        "telegramUsername": safe_id("telegramUsername", raw.get("telegramUsername"), required=False),
        "telegramDisplayName": safe_text("telegramDisplayName", raw.get("telegramDisplayName"), 160),
    }


def mutation_idempotency(args: dict[str, Any], actor: dict[str, str | None], task_code: str | None = None) -> str:
    chat_id = actor.get("telegramChatId")
    message_id = actor.get("telegramMessageId")
    if chat_id and message_id:
        suffix = f"-{task_code}" if task_code else ""
        return safe_id("idempotencyKey", f"telegram-{chat_id}-{message_id}{suffix}")
    return safe_id("idempotencyKey", args.get("idempotencyKey"))


def base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def mint_operations_token(config: dict[str, Any]) -> str:
    secret = read_secret(config, "OW_AGENT_TOKEN_SECRET", "OW_AGENT_TOKEN_SECRET_FILE")
    now = int(time.time())
    prop = property_id(config)
    payload = {
        "typ": "agent_access",
        "iss": "owhub",
        "aud": "operations",
        "agentId": "finca",
        "credentialId": "finca-operations-v1",
        "actorLabel": "OW Finca",
        "permissions": ["operations.read", "operations.write"],
        "propertyIds": [prop],
        "activePropertyId": prop,
        "allowedToolClassifications": ["read", "guarded_write"],
        "allowedTools": OPERATIONS_TOOLS,
        "iat": now,
        "exp": now + 300,
        "jti": str(uuid.uuid4()),
    }
    encoded = base64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode())
    signature = base64url(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def parse_http_error(exc: urllib.error.HTTPError, default_code: str) -> ToolError:
    try:
        payload = json.loads(exc.read().decode() or "{}")
    except Exception:
        payload = {}
    code = payload.get("errorCode") or payload.get("code") or default_code
    message = payload.get("message") or payload.get("error") or f"Upstream request failed with HTTP {exc.code}."
    if not isinstance(code, str):
        code = default_code
    if not isinstance(message, str):
        message = f"Upstream request failed with HTTP {exc.code}."
    retryable = exc.code in (408, 409, 425, 429) or 500 <= exc.code <= 599
    return ToolError(code.lower(), message[:500], retryable=retryable)


def http_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 45) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise parse_http_error(exc, "upstream_http_error") from exc
    except urllib.error.URLError as exc:
        raise ToolError("network_error", "The upstream service could not be reached.", retryable=True) from exc


def operations_tool(config: dict[str, Any], name: str, payload: dict[str, Any]) -> Any:
    if name not in OPERATIONS_TOOLS:
        raise ToolError("tool_not_allowed", "This Operations tool is not allowed for Finca.")
    if mock_enabled(config):
        return mock_tool(name, payload)
    response = http_json(
        f"{operations_base_url(config)}/api/tools",
        {"tool": name, "input": payload},
        {
            "Authorization": f"Bearer {mint_operations_token(config)}",
            "x-ow-active-property-id": property_id(config),
            "x-ow-request-source": "internal_agent",
            "x-ow-correlation-id": str(uuid.uuid4()),
        },
    )
    if response.get("success") is not True:
        raise ToolError(
            str(response.get("errorCode") or "operations_error").lower(),
            str(response.get("message") or "Operations rejected the task request.")[:500],
        )
    return response.get("data")


def locked_mock_state() -> tuple[Any, dict[str, Any]]:
    MOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = MOCK_FILE.with_suffix(".lock")
    handle = lock_path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    try:
        state = json.loads(MOCK_FILE.read_text()) if MOCK_FILE.exists() else {"nextSequence": 1, "workers": [], "tasks": []}
    except json.JSONDecodeError:
        state = {"nextSequence": 1, "workers": [], "tasks": []}
    return handle, state


def save_mock_state(handle: Any, state: dict[str, Any]) -> None:
    temp = MOCK_FILE.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    os.replace(temp, MOCK_FILE)
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


def release_mock_state(handle: Any) -> None:
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


def mock_actor_worker(state: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any] | None:
    user_id = actor.get("telegramUserId")
    if not user_id:
        return None
    for worker in state["workers"]:
        if worker.get("telegramUserId") == user_id:
            worker["displayName"] = actor.get("telegramDisplayName") or worker["displayName"]
            return worker
    worker = {
        "id": str(uuid.uuid4()),
        "displayName": actor.get("telegramDisplayName") or actor.get("telegramUsername") or user_id,
        "telegramUserId": user_id,
        "telegramHandle": actor.get("telegramUsername"),
        "active": True,
    }
    state["workers"].append(worker)
    return worker


def mock_resolve_worker(state: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    worker_id = payload.get("assigneeWorkerId")
    worker_name = payload.get("assigneeName")
    if not worker_id and not worker_name:
        return None
    matches = [
        worker
        for worker in state["workers"]
        if worker.get("active", True)
        and (worker.get("id") == worker_id or (worker_name and worker_name.lower() in worker.get("displayName", "").lower()))
    ]
    if len(matches) != 1:
        raise ToolError("worker_ambiguous" if matches else "worker_not_found", "The assignee could not be resolved uniquely.")
    return matches[0]


def mock_task_view(task: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    worker = next((item for item in state["workers"] if item["id"] == task.get("assignedWorkerId")), None)
    return {**task, "assignee": worker, "attachmentCount": len(task.get("attachments", []))}


def mock_tool(name: str, payload: dict[str, Any]) -> Any:
    handle, state = locked_mock_state()
    try:
        if name == "operations.finca.list_workers":
            query = str(payload.get("query") or "").lower()
            result = [worker for worker in state["workers"] if worker.get("active", True) and query in worker["displayName"].lower()]
            release_mock_state(handle)
            return result

        if name == "operations.finca.list_tasks":
            statuses = set(payload.get("statuses") or [])
            if not statuses and not payload.get("includeCompleted"):
                statuses = {"open", "in_progress", "blocked"}
            query = str(payload.get("query") or "").lower()
            telegram_user = payload.get("telegramUserId")
            assigned_worker = next((item for item in state["workers"] if item.get("telegramUserId") == telegram_user), None) if telegram_user else None
            tasks = []
            for task in state["tasks"]:
                if statuses and task["status"] not in statuses:
                    continue
                if payload.get("priority") is not None and bool(payload["priority"]) != task["priority"]:
                    continue
                if query and query not in f"{task['code']} {task['title']} {task.get('details') or ''}".lower():
                    continue
                if telegram_user and (not assigned_worker or task.get("assignedWorkerId") != assigned_worker["id"]):
                    continue
                tasks.append(mock_task_view(task, state))
            tasks.sort(key=lambda task: (not task["priority"], task["status"], task["updatedAt"]), reverse=False)
            result = tasks[: int(payload.get("limit") or 200)]
            release_mock_state(handle)
            return result

        if name == "operations.finca.get_task":
            code = safe_task_code(payload.get("taskCode"))
            task = next((item for item in state["tasks"] if item["code"] == code), None)
            if not task:
                raise ToolError("task_not_found", "Finca task was not found.")
            result = mock_task_view(task, state)
            release_mock_state(handle)
            return result

        if name == "operations.finca.create_task":
            key = safe_id("idempotencyKey", payload.get("idempotencyKey"))
            existing = next((item for item in state["tasks"] if item.get("idempotencyKey") == key), None)
            if existing:
                result = {"duplicate": True, "task": mock_task_view(existing, state)}
                release_mock_state(handle)
                return result
            title = safe_text("title", payload.get("title"), 240, required=True)
            actor = normalize_actor(payload.get("actor"))
            mock_actor_worker(state, actor)
            assignee = mock_resolve_worker(state, payload)
            sequence = int(state["nextSequence"])
            state["nextSequence"] = sequence + 1
            timestamp = now_iso()
            task = {
                "id": str(uuid.uuid4()),
                "code": f"F-{sequence:04d}",
                "title": title,
                "details": safe_text("details", payload.get("details"), 4000),
                "estimatedMinutes": safe_optional_int(
                    "estimatedMinutes",
                    payload.get("estimatedMinutes"),
                    1,
                    MAX_ESTIMATED_MINUTES,
                ),
                "priority": safe_bool("priority", payload.get("priority"), False),
                "status": "open",
                "progressPercent": 0,
                "blockedReason": None,
                "assignedWorkerId": assignee["id"] if assignee else None,
                "idempotencyKey": key,
                "version": 1,
                "events": [{"eventType": "created", "actorName": actor.get("telegramDisplayName") or "OW Finca", "createdAt": timestamp}],
                "attachments": [],
                "createdAt": timestamp,
                "updatedAt": timestamp,
            }
            state["tasks"].append(task)
            save_mock_state(handle, state)
            return {"duplicate": False, "task": mock_task_view(task, state)}

        if name == "operations.finca.update_task":
            code = safe_task_code(payload.get("taskCode"))
            task = next((item for item in state["tasks"] if item["code"] == code), None)
            if not task:
                raise ToolError("task_not_found", "Finca task was not found.")
            key = safe_id("idempotencyKey", payload.get("idempotencyKey"))
            if any(event.get("idempotencyKey") == key for event in task["events"]):
                result = {"duplicate": True, "task": mock_task_view(task, state)}
                release_mock_state(handle)
                return result
            action = safe_text("action", payload.get("action"), 40, required=True)
            if action not in ALLOWED_ACTIONS:
                raise ToolError("invalid_action", "Task action is not supported.")
            if task["status"] in ("completed", "cancelled") and action in ("start", "progress", "block"):
                raise ToolError("reopen_required", "Completed or cancelled tasks must be reopened first.")
            if action == "start":
                task["status"] = "in_progress"
                task["blockedReason"] = None
            elif action == "progress":
                progress = int(payload.get("progressPercent"))
                if progress < 0 or progress > 100:
                    raise ToolError("invalid_progress", "Progress must be from 0 to 100.")
                task["progressPercent"] = progress
                task["status"] = "completed" if progress == 100 else "in_progress"
                task["blockedReason"] = None
            elif action == "block":
                task["blockedReason"] = safe_text("blockedReason", payload.get("blockedReason"), 1000, required=True)
                task["status"] = "blocked"
            elif action == "complete":
                task["status"] = "completed"
                task["progressPercent"] = 100
                task["blockedReason"] = None
            elif action == "reopen":
                if task["status"] not in ("completed", "cancelled"):
                    raise ToolError("not_closed", "Only a completed or cancelled task can be reopened.")
                task["status"] = "open"
                task["progressPercent"] = 0
                task["blockedReason"] = None
            elif action == "cancel":
                task["status"] = "cancelled"
                task["blockedReason"] = None
            elif action == "assign":
                task["assignedWorkerId"] = None if payload.get("clearAssignee") else mock_resolve_worker(state, payload)["id"]
            elif action == "priority":
                task["priority"] = safe_bool("priority", payload.get("priority"))
            actor = normalize_actor(payload.get("actor"))
            mock_actor_worker(state, actor)
            task["version"] += 1
            task["updatedAt"] = now_iso()
            task["events"].append({
                "eventType": action,
                "actorName": actor.get("telegramDisplayName") or "OW Finca",
                "note": payload.get("note") or payload.get("blockedReason"),
                "idempotencyKey": key,
                "createdAt": task["updatedAt"],
            })
            save_mock_state(handle, state)
            return {"duplicate": False, "task": mock_task_view(task, state)}

        if name == "operations.finca.daily_report":
            release_mock_state(handle)
            return build_mock_report()

        raise ToolError("tool_not_implemented", "Mock tool is not implemented.")
    except Exception:
        if not handle.closed:
            release_mock_state(handle)
        raise


def status_label(task: dict[str, Any]) -> str:
    return {"open": "Pendiente", "in_progress": "En progreso", "blocked": "Bloqueada"}.get(task["status"], task["status"])


def report_line(task: dict[str, Any]) -> str:
    assignee = (task.get("assignee") or {}).get("displayName") or "Sin responsable"
    progress = f" · {task['progressPercent']}%" if task.get("progressPercent", 0) > 0 else ""
    lines = [f"{task['code']} · {task['title']}", f"{assignee}{progress} · {status_label(task)}"]
    if task["status"] == "blocked" and task.get("blockedReason"):
        lines.append(f"Bloqueada: {task['blockedReason']}")
    return "\n".join(lines)


def worker_safe_report_message(message: str) -> str:
    """Hide internal codes and empty/default fields from worker reports."""
    scrubbed = WORKER_REPORT_CODE_RE.sub(r"\1", message)
    cleaned_lines: list[str] = []
    hidden_parts = {"sin asignar", "sin responsable", "0%"}
    for line in scrubbed.splitlines():
        if "\u00b7" not in line:
            if line.strip().casefold() not in hidden_parts:
                cleaned_lines.append(line)
            continue
        parts = [part.strip() for part in line.split("\u00b7")]
        visible = [part for part in parts if part.casefold() not in hidden_parts]
        if visible:
            cleaned_lines.append(" \u00b7 ".join(visible))
    return "\n".join(cleaned_lines)


def split_messages(header: str, sections: list[str], max_length: int = 3800) -> list[str]:
    messages: list[str] = []
    current = header
    for section in sections:
        candidate = f"{current}\n\n{section}"
        if len(candidate) > max_length and current != header:
            messages.append(current)
            current = f"{header} (continuación)\n\n{section}"
        else:
            current = candidate
    messages.append(current)
    return messages


def build_mock_report() -> dict[str, Any]:
    handle, state = locked_mock_state()
    release_mock_state(handle)
    tasks = [mock_task_view(task, state) for task in state["tasks"] if task["status"] in ("open", "in_progress", "blocked")]
    if not tasks:
        return {"messages": ["No hay tareas pendientes en la finca."], "counts": {"total": 0}}
    priority = [task for task in tasks if task["priority"]]
    remaining = [task for task in tasks if not task["priority"]]
    sections = []
    for heading, items in (
        ("Prioridad", priority),
        ("En progreso", [task for task in remaining if task["status"] == "in_progress"]),
        ("Pendientes", [task for task in remaining if task["status"] == "open"]),
        ("Bloqueadas", [task for task in remaining if task["status"] == "blocked"]),
    ):
        if items:
            sections.append(f"{heading}\n" + "\n\n".join(report_line(task) for task in items))
    today = dt.datetime.now().astimezone().strftime("%d/%m/%Y")
    return {"messages": split_messages(f"Tareas de la finca — {today}", sections), "counts": {"total": len(tasks)}}


def tool_list(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    payload = {
        "statuses": args.get("statuses"),
        "priority": args.get("priority"),
        "assigneeWorkerId": safe_id("assigneeWorkerId", args.get("assigneeWorkerId"), required=False),
        "telegramUserId": safe_id("telegramUserId", args.get("telegramUserId"), required=False),
        "query": safe_text("query", args.get("query"), 240),
        "includeCompleted": safe_bool("includeCompleted", args.get("includeCompleted"), False),
        "limit": max(1, min(500, int(args.get("limit") or 200))),
    }
    statuses = payload["statuses"]
    if statuses is not None:
        if not isinstance(statuses, list) or any(status not in ALLOWED_STATUSES for status in statuses):
            raise ToolError("invalid_input", "statuses contains an unsupported value.")
    return {"ok": True, "tasks": operations_tool(config, "operations.finca.list_tasks", payload)}


def tool_get(args: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "task": operations_tool(load_config(), "operations.finca.get_task", {"taskCode": safe_task_code(args.get("taskCode"))})}


def tool_create(args: dict[str, Any]) -> dict[str, Any]:
    actor = normalize_actor(args.get("actor"))
    payload = {
        "title": safe_text("title", args.get("title"), 240, required=True),
        "details": safe_text("details", args.get("details"), 4000),
        "estimatedMinutes": safe_optional_int(
            "estimatedMinutes",
            args.get("estimatedMinutes"),
            1,
            MAX_ESTIMATED_MINUTES,
        ),
        "priority": safe_bool("priority", args.get("priority"), False),
        "assigneeWorkerId": safe_id("assigneeWorkerId", args.get("assigneeWorkerId"), required=False),
        "assigneeName": safe_text("assigneeName", args.get("assigneeName"), 160),
        "source": "telegram",
        "idempotencyKey": mutation_idempotency(args, actor),
        "actor": actor,
    }
    return {"ok": True, **operations_tool(load_config(), "operations.finca.create_task", payload)}


def tool_update(args: dict[str, Any]) -> dict[str, Any]:
    action = safe_text("action", args.get("action"), 40, required=True)
    if action not in ALLOWED_ACTIONS:
        raise ToolError("invalid_input", "action is not supported.")
    progress = args.get("progressPercent")
    if progress is not None:
        if not isinstance(progress, int) or not 0 <= progress <= 100:
            raise ToolError("invalid_input", "progressPercent must be an integer from 0 to 100.")
    task_code = safe_task_code(args.get("taskCode"))
    actor = normalize_actor(args.get("actor"))
    payload = {
        "taskCode": task_code,
        "action": action,
        "progressPercent": progress,
        "blockedReason": safe_text("blockedReason", args.get("blockedReason"), 1000),
        "priority": args.get("priority"),
        "assigneeWorkerId": safe_id("assigneeWorkerId", args.get("assigneeWorkerId"), required=False),
        "assigneeName": safe_text("assigneeName", args.get("assigneeName"), 160),
        "clearAssignee": safe_bool("clearAssignee", args.get("clearAssignee"), False),
        "note": safe_text("note", args.get("note"), 2000),
        "idempotencyKey": mutation_idempotency(args, actor, task_code),
        "actor": actor,
    }
    return {"ok": True, **operations_tool(load_config(), "operations.finca.update_task", payload)}


def contained_media_path(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ToolError("invalid_input", "Each media path must be text.")
    path = Path(value).expanduser().resolve()
    roots = [INBOUND_MEDIA_ROOT.resolve(), SPOOL_ROOT.resolve(), ALBUM_ROOT.resolve()]
    if not any(path == root or root in path.parents for root in roots):
        raise ToolError("invalid_input", "Media paths must be inside the Finca inbound media or spool directories.")
    if not path.is_file():
        raise ToolError("file_not_found", "An inbound photo was not found.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ToolError("file_too_large", "A photo is larger than 10 MB.")
    return path


def extension_for(path: Path, content_type: str | None = None) -> str:
    extension = path.suffix.lower()
    if extension in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"):
        return extension
    return {"image/png": ".png", "image/webp": ".webp", "image/heic": ".heic", "image/heif": ".heif"}.get(content_type or "", ".jpg")


def telegram_download(config: dict[str, Any], file_id: str, destination: Path) -> Path:
    token = telegram_token(config)
    meta = http_json(
        f"https://api.telegram.org/bot{token}/getFile",
        {"file_id": file_id},
        {},
    )
    if meta.get("ok") is not True or not isinstance(meta.get("result"), dict):
        raise ToolError("telegram_get_file_failed", "Telegram could not resolve the photo.", retryable=True)
    file_path = meta["result"].get("file_path")
    if not isinstance(file_path, str) or not file_path:
        raise ToolError("telegram_get_file_failed", "Telegram returned no photo path.", retryable=True)
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(MAX_FILE_BYTES + 1)
            content_type = response.headers.get_content_type()
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        raise ToolError("telegram_download_failed", "Telegram photo download failed.", retryable=True) from exc
    if len(data) > MAX_FILE_BYTES:
        raise ToolError("file_too_large", "A Telegram photo is larger than 10 MB.")
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise ToolError("unsupported_file", "Telegram returned an unsupported photo type.")
    destination = destination.with_suffix(extension_for(destination, content_type))
    destination.write_bytes(data)
    return destination


def spool_inputs(config: dict[str, Any], args: dict[str, Any], target_dir: Path) -> list[Path]:
    media_paths = args.get("openclawMediaPaths") or []
    file_ids = args.get("fileIds") or []
    if not isinstance(media_paths, list) or not isinstance(file_ids, list):
        raise ToolError("invalid_input", "Photo inputs must be arrays.")
    if not 1 <= len(media_paths) + len(file_ids) <= MAX_FILES:
        raise ToolError("invalid_input", "Provide 1 to 10 photos.")
    target_dir.mkdir(parents=True, exist_ok=True)
    output: list[Path] = []
    index = 1
    for raw in media_paths:
        source = contained_media_path(raw)
        destination = target_dir / f"original-{index}{extension_for(source)}"
        if source != destination:
            shutil.copy2(source, destination)
        output.append(destination)
        index += 1
    for raw in file_ids:
        file_id = safe_id("fileId", raw)
        destination = telegram_download(config, file_id, target_dir / f"original-{index}.jpg")
        output.append(destination)
        index += 1
    return output


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(temp, path)


def album_spool(config: dict[str, Any], args: dict[str, Any], task_code: str) -> tuple[bool, list[Path], str]:
    media_group = safe_id("mediaGroupId", args.get("mediaGroupId"))
    chat_id = safe_id("telegramChatId", (args.get("actor") or {}).get("telegramChatId"))
    group_dir = ALBUM_ROOT / chat_id / media_group
    lock_path = group_dir / "state.lock"
    group_dir.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state_path = group_dir / "state.json"
        try:
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
        except json.JSONDecodeError:
            state = {}
        if state.get("taskCode") and state["taskCode"] != task_code:
            raise ToolError("album_task_mismatch", "One Telegram album cannot be attached to different tasks.")
        source_message_id = safe_id("sourceMessageId", args.get("sourceMessageId"))
        seen_messages = state.get("sourceMessageIds") if isinstance(state.get("sourceMessageIds"), list) else []
        existing_count = len(list(group_dir.glob("original-*")))
        if source_message_id not in seen_messages:
            arrivals = spool_inputs(config, args, group_dir / f"arrival-{source_message_id}")
            for source in arrivals:
                destination = group_dir / f"original-{existing_count + 1}{source.suffix.lower()}"
                if not destination.exists():
                    shutil.copy2(source, destination)
                    existing_count += 1
            seen_messages.append(source_message_id)
            state["lastArrivalAt"] = time.time()
        state.update({"taskCode": task_code, "sourceMessageIds": seen_messages, "photoCount": existing_count})
        atomic_json(state_path, state)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    quiet_seconds = float(args.get("quietSeconds") or 5.5)
    quiet_seconds = min(15.0, max(5.0, quiet_seconds))
    deadline = time.time() + 30
    while time.time() < deadline:
        state = json.loads((group_dir / "state.json").read_text())
        remaining = quiet_seconds - (time.time() - float(state.get("lastArrivalAt", 0)))
        if remaining <= 0:
            break
        time.sleep(min(remaining, 1.0))

    claim_path = group_dir / "claimed.json"
    try:
        descriptor = os.open(claim_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump({"claimOwner": safe_id("claimOwner", args.get("claimOwner")), "claimedAt": now_iso()}, handle)
    except FileExistsError:
        return False, [], "claimed_by_another_run"
    return True, sorted(group_dir.glob("original-*")), "claimed"


def multipart_body(fields: dict[str, str], files: list[Path]) -> tuple[bytes, str]:
    boundary = f"----ow-finca-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode(),
            b"\r\n",
        ])
    for path in files:
        content_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        if content_type not in ALLOWED_IMAGE_TYPES:
            content_type = "image/jpeg"
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            path.read_bytes(),
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def upload_photos(config: dict[str, Any], task_code: str, files: list[Path], args: dict[str, Any]) -> dict[str, Any]:
    if mock_enabled(config):
        return {
            "attachments": [
                {"id": str(uuid.uuid4()), "localPath": str(path), "fileName": path.name, "attachmentType": args.get("attachmentType") or "progress_photo"}
                for path in files
            ]
        }
    actor = normalize_actor(args.get("actor"))
    fields = {
        "attachmentType": args.get("attachmentType") or "progress_photo",
        "sourceTelegramMessageId": safe_id("sourceMessageId", args.get("sourceMessageId")),
        "idempotencyKeyPrefix": safe_id("idempotencyKeyPrefix", args.get("idempotencyKeyPrefix")),
        "actorJson": json.dumps(actor, separators=(",", ":")),
    }
    body, boundary = multipart_body(fields, files)
    request = urllib.request.Request(
        f"{operations_base_url(config)}/api/finca/tasks/{urllib.parse.quote(task_code)}/attachments/upload",
        data=body,
        headers={
            "Authorization": f"Bearer {mint_operations_token(config)}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "x-ow-active-property-id": property_id(config),
            "x-ow-request-source": "internal_agent",
            "x-ow-correlation-id": str(uuid.uuid4()),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise parse_http_error(exc, "attachment_upload_failed") from exc
    except urllib.error.URLError as exc:
        raise ToolError("attachment_upload_failed", "Operations photo upload could not be reached.", retryable=True) from exc
    if result.get("success") is not True and result.get("ok") is not True:
        raise ToolError("attachment_upload_failed", str(result.get("message") or result.get("error") or "Operations rejected the photos."))
    return result.get("data") or result


def tool_attach_photos(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    task_code = safe_task_code(args.get("taskCode"))
    source_message = safe_id("sourceMessageId", args.get("sourceMessageId"))
    safe_id("idempotencyKeyPrefix", args.get("idempotencyKeyPrefix"))
    attachment_type = args.get("attachmentType") or "progress_photo"
    if attachment_type not in ("progress_photo", "completion_photo"):
        raise ToolError("invalid_input", "attachmentType is not supported.")
    album_claim_path: Path | None = None
    if args.get("mediaGroupId"):
        claimed, paths, reason = album_spool(config, args, task_code)
        if not claimed:
            return {"ok": True, "status": "not_claimed", "reason": reason}
        spool_dir = paths[0].parent if paths else ALBUM_ROOT
        album_claim_path = spool_dir / "claimed.json"
    else:
        spool_dir = SPOOL_ROOT / task_code / source_message
        paths = spool_inputs(config, args, spool_dir)
    if not paths:
        raise ToolError("photo_missing", "No photos were available after spooling.")
    pending_path = spool_dir / "pending-upload.json"
    atomic_json(pending_path, {"taskCode": task_code, "sourceMessageId": source_message, "files": [str(path) for path in paths], "createdAt": now_iso()})
    try:
        uploaded = upload_photos(config, task_code, paths, args)
    except Exception:
        if album_claim_path:
            album_claim_path.unlink(missing_ok=True)
        raise
    pending_path.unlink(missing_ok=True)
    return {"ok": True, "status": "attached", **uploaded, "spooledFiles": len(paths)}


def telegram_send(config: dict[str, Any], chat_id: str, text: str, reply_to: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    response = http_json(f"https://api.telegram.org/bot{telegram_token(config)}/sendMessage", payload, {})
    if response.get("ok") is not True:
        raise ToolError("telegram_send_failed", "Telegram rejected the Finca message.", retryable=True)
    result = response.get("result") or {}
    return {"ok": True, "messageId": result.get("message_id")}


def tool_send_message(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    chat_id = safe_id("chatId", args.get("chatId"), required=False) or notify_chat_id(config)
    return telegram_send(
        config,
        chat_id,
        safe_text("text", args.get("text"), 4096, required=True),
        safe_id("replyToMessageId", args.get("replyToMessageId"), required=False),
    )


def tool_daily_report(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    report = operations_tool(config, "operations.finca.daily_report", {"timezone": "America/Bogota"})
    messages = report.get("messages") if isinstance(report, dict) else None
    if not isinstance(messages, list) or not messages or any(not isinstance(item, str) for item in messages):
        raise ToolError("invalid_report", "Operations returned an invalid Finca report.")
    messages = [worker_safe_report_message(message) for message in messages]
    if safe_bool("dryRun", args.get("dryRun"), False):
        return {"ok": True, "sent": False, "messages": messages, "counts": report.get("counts", {})}
    sent = [telegram_send(config, notify_chat_id(config), message) for message in messages]
    return {"ok": True, "sent": True, "messageCount": len(sent), "messageIds": [item.get("messageId") for item in sent], "counts": report.get("counts", {})}


ACTOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "telegramChatId": {"type": ["string", "number", "null"]},
        "telegramUserId": {"type": ["string", "number", "null"]},
        "telegramMessageId": {"type": ["string", "number", "null"]},
        "telegramUsername": {"type": ["string", "null"]},
        "telegramDisplayName": {"type": ["string", "null"]},
    },
}


TOOLS: dict[str, tuple[str, dict[str, Any], Callable[[dict[str, Any]], dict[str, Any]]]] = {
    "finca_tasks_list": (
        "List current OW Finca tasks from Operations. Supports all tasks, filters, and the current worker's assignments.",
        {"type": "object", "properties": {"statuses": {"type": "array", "items": {"type": "string", "enum": sorted(ALLOWED_STATUSES)}}, "priority": {"type": ["boolean", "null"]}, "assigneeWorkerId": {"type": ["string", "null"]}, "telegramUserId": {"type": ["string", "number", "null"]}, "query": {"type": ["string", "null"]}, "includeCompleted": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, "additionalProperties": False},
        tool_list,
    ),
    "finca_tasks_get": (
        "Get one OW Finca task using the internal stable code returned by finca_tasks_list. Never ask a worker to provide this code.",
        {"type": "object", "properties": {"taskCode": {"type": "string"}}, "required": ["taskCode"], "additionalProperties": False},
        tool_get,
    ),
    "finca_tasks_create": (
        "Create one OW Finca task in Operations with server-enforced idempotency.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 240},
                "details": {"type": ["string", "null"], "maxLength": 4000},
                "estimatedMinutes": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "maximum": MAX_ESTIMATED_MINUTES,
                    "description": "Optional estimated effort in whole minutes. This is not a due date.",
                },
                "priority": {"type": "boolean"},
                "assigneeWorkerId": {"type": ["string", "null"]},
                "assigneeName": {"type": ["string", "null"]},
                "idempotencyKey": {"type": "string"},
                "actor": ACTOR_SCHEMA,
            },
            "required": ["title", "idempotencyKey"],
            "additionalProperties": False,
        },
        tool_create,
    ),
    "finca_tasks_update": (
        "Apply one audited action using the internal task code resolved from the worker's description and finca_tasks_list.",
        {
            "type": "object",
            "properties": {
                "taskCode": {"type": "string"},
                "action": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)},
                "progressPercent": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
                "blockedReason": {"type": ["string", "null"], "maxLength": 1000},
                "priority": {"type": ["boolean", "null"]},
                "assigneeWorkerId": {"type": ["string", "null"]},
                "assigneeName": {"type": ["string", "null"]},
                "clearAssignee": {"type": "boolean"},
                "note": {"type": ["string", "null"], "maxLength": 2000},
                "idempotencyKey": {"type": "string"},
                "actor": ACTOR_SCHEMA,
            },
            "required": ["taskCode", "action", "idempotencyKey"],
            "additionalProperties": False,
        },
        tool_update,
    ),
    "finca_tasks_attach_photos": (
        "Durably spool Telegram progress/completion photos and attach them using the internal task code resolved from the caption or reply context.",
        {
            "type": "object",
            "properties": {
                "taskCode": {"type": "string"},
                "sourceMessageId": {"type": ["string", "number"]},
                "openclawMediaPaths": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_FILES},
                "fileIds": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_FILES},
                "attachmentType": {"type": "string", "enum": ["progress_photo", "completion_photo"]},
                "idempotencyKeyPrefix": {"type": "string"},
                "mediaGroupId": {"type": ["string", "null"]},
                "claimOwner": {"type": ["string", "null"]},
                "quietSeconds": {"type": "number", "minimum": 5, "maximum": 15},
                "actor": ACTOR_SCHEMA,
            },
            "required": ["taskCode", "sourceMessageId", "idempotencyKeyPrefix"],
            "additionalProperties": False,
        },
        tool_attach_photos,
    ),
    "finca_tasks_send_daily_report": (
        "Fetch the deterministic outstanding-task report from Operations and send it to the configured OW Finca Telegram group.",
        {"type": "object", "properties": {"dryRun": {"type": "boolean"}}, "additionalProperties": False},
        tool_daily_report,
    ),
    "finca_telegram_send_message": (
        "Send one direct Bot API message to the configured or supplied OW Finca chat.",
        {"type": "object", "properties": {"chatId": {"type": ["string", "number"]}, "text": {"type": "string", "maxLength": 4096}, "replyToMessageId": {"type": ["string", "number", "null"]}}, "required": ["text"], "additionalProperties": False},
        tool_send_message,
    ),
}


def catalog() -> list[dict[str, Any]]:
    return [
        {"name": name, "description": description, "inputSchema": schema}
        for name, (description, schema, _handler) in TOOLS.items()
    ]


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": {"code": "usage", "message": "Use list or call <tool>."}}))
        return 2
    if sys.argv[1] == "list":
        print(json.dumps(catalog(), ensure_ascii=False))
        return 0
    if sys.argv[1] != "call" or len(sys.argv) != 3 or sys.argv[2] not in TOOLS:
        print(json.dumps({"ok": False, "error": {"code": "unknown_tool", "message": "Unknown Finca tool."}}))
        return 2
    try:
        raw = sys.stdin.read()
        args = json.loads(raw or "{}")
        if not isinstance(args, dict):
            raise ToolError("invalid_input", "Tool arguments must be an object.")
        output = TOOLS[sys.argv[2]][2](args)
    except Exception as exc:
        output = result_error(exc)
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    return 0 if output.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
