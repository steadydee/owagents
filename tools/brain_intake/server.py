#!/usr/bin/env python3
"""Narrow MCP tools for routing OpenClaw Telegram messages into Brain.

The tool reads runtime secrets from OpenClaw config or environment, never from
tool arguments. It accepts text updates, submits them to Brain Intake, and can
return the Brain receipt to the same Telegram chat.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

CONFIG_PATH = Path(os.environ.get("OPENCLAW_CONFIG_PATH", "~/.openclaw-owlswatch/openclaw.json")).expanduser()

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,160}$")
TEXT_RE = re.compile(r"^[\s\S]{0,12000}$")
TELEGRAM_MAX_TEXT = 3900


class ToolError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def sanitized_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ToolError):
        return {"ok": False, "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable}}
    return {"ok": False, "error": {"code": "internal_error", "message": "Tool failed without exposing sensitive details.", "retryable": False}}


def load_config() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ToolError("config_invalid", f"OpenClaw config is not valid JSON: line {exc.lineno}") from exc


def cfg_env(config: dict[str, Any], key: str) -> str | None:
    value = os.environ.get(key)
    if value:
        return value
    mcp_env = config.get("mcp", {}).get("servers", {}).get("brain_intake", {}).get("env", {})
    value = mcp_env.get(key)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    env_vars = config.get("env", {}).get("vars", {})
    value = env_vars.get(key)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    return None


def telegram_token(config: dict[str, Any]) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    token = config.get("channels", {}).get("telegram", {}).get("botToken")
    if isinstance(token, str) and token and not token.startswith("<"):
        return token
    raise ToolError("config_missing", "Telegram bot token is missing from tool environment/config.")


def brain_base_url(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "BRAIN_API_BASE_URL") or "http://127.0.0.1:3000"
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme == "https" and parsed.netloc:
        return raw.rstrip("/")
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return raw.rstrip("/")
    raise ToolError("config_invalid", "Brain API base URL must be https or local http.")


def validate_safe_id(name: str, value: Any, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise ToolError("invalid_input", f"{name} is required.")
        return None
    if not isinstance(value, (str, int)):
        raise ToolError("invalid_input", f"{name} must be a string or integer.")
    text = str(value)
    if not SAFE_ID_RE.match(text):
        raise ToolError("invalid_input", f"{name} is malformed.")
    return text


def validate_text(name: str, value: Any, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ToolError("invalid_input", f"{name} is required.")
        return None
    if not isinstance(value, str) or not TEXT_RE.match(value):
        raise ToolError("invalid_input", f"{name} is malformed.")
    return value


def http_json_post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 45) -> dict[str, Any]:
    body = json_dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        retryable = 500 <= exc.code <= 599 or exc.code in (408, 409, 425, 429)
        raise ToolError("http_error", f"Brain request failed with HTTP {exc.code}.", retryable=retryable) from exc
    except urllib.error.URLError as exc:
        raise ToolError("network_error", "Brain request failed due to a network error.", retryable=True) from exc


def http_json_get(url: str, headers: dict[str, str], timeout: int = 15) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        retryable = 500 <= exc.code <= 599 or exc.code in (408, 409, 425, 429)
        raise ToolError("http_error", f"Brain request failed with HTTP {exc.code}.", retryable=retryable) from exc
    except urllib.error.URLError as exc:
        raise ToolError("network_error", "Brain request failed due to a network error.", retryable=True) from exc


def brain_headers(config: dict[str, Any]) -> dict[str, str]:
    api_token = cfg_env(config, "BRAIN_API_TOKEN")
    if api_token:
        return {"Authorization": f"Bearer {api_token}"}
    admin_token = cfg_env(config, "BRAIN_ADMIN_TOKEN")
    return {"x-brain-admin-token": admin_token} if admin_token else {}


def telegram_api(method: str, params: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    token = telegram_token(config)
    url = f"https://api.telegram.org/bot{urllib.parse.quote(token)}/{method}"
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise ToolError("telegram_http_error", f"Telegram request failed with HTTP {exc.code}.", retryable=500 <= exc.code <= 599) from exc
    except urllib.error.URLError as exc:
        raise ToolError("telegram_network_error", "Telegram request failed due to a network error.", retryable=True) from exc
    if not data.get("ok"):
        raise ToolError("telegram_error", "Telegram returned an error.", retryable=False)
    return data["result"]


def build_intake_payload(args: dict[str, Any]) -> dict[str, Any]:
    raw_text = validate_text("raw_text", args.get("raw_text"), required=True)
    sender_name = validate_text("sender_name", args.get("sender_name")) or "Telegram"
    sender_id = validate_safe_id("sender_id", args.get("sender_id"), required=False)
    chat_id = validate_safe_id("chat_id", args.get("chat_id"), required=False)
    chat_title = validate_text("chat_title", args.get("chat_title"))
    message_id = validate_safe_id("message_id", args.get("message_id"), required=False)
    message_thread_id = validate_safe_id("message_thread_id", args.get("message_thread_id"), required=False)
    external_source_id = validate_safe_id("external_source_id", args.get("external_source_id"), required=False)
    project_hint = validate_text("project_hint", args.get("project_hint"))
    domain_hint = validate_text("domain_hint", args.get("domain_hint"))
    sender = f"{sender_name} ({sender_id})" if sender_id else sender_name
    if external_source_id is None and chat_id and message_id:
        thread_part = message_thread_id or "main"
        external_source_id = f"telegram_openclaw:{chat_id}:{thread_part}:{message_id}"
    metadata = {
        "openclaw_profile": "owlswatch",
        "openclaw_route": "dennis_brain",
        "telegram_chat_title": chat_title,
        "telegram_chat_id": chat_id,
        "telegram_message_id": message_id,
        "telegram_thread_id": message_thread_id,
        "externalSourceId": external_source_id,
    }
    payload = {
        "raw_text": raw_text,
        "source": "telegram_openclaw",
        "external_source_id": external_source_id,
        "channel": "telegram",
        "sender": sender,
        "timestamp": now_iso(),
        "project_hint": project_hint,
        "domain_hint": domain_hint,
        "metadata": {key: value for key, value in metadata.items() if value is not None},
    }
    return {key: value for key, value in payload.items() if value is not None}


def trim_telegram_text(text: str) -> str:
    if len(text) <= TELEGRAM_MAX_TEXT:
        return text
    return text[: TELEGRAM_MAX_TEXT - 80].rstrip() + "\n\n[Receipt shortened for Telegram. Open Brain for full details.]"


def tool_brain_health_check(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    data = http_json_get(f"{brain_base_url(config)}/api/health", brain_headers(config))
    return {
        "ok": bool(data.get("ok")),
        "service": data.get("service"),
        "storage": data.get("storage"),
        "timestamp": data.get("timestamp"),
    }


def tool_brain_submit_intake(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    payload = build_intake_payload(args)
    data = http_json_post(f"{brain_base_url(config)}/api/brain/intake", payload, brain_headers(config), timeout=60)
    receipt = data.get("receipt")
    if not isinstance(receipt, str) or not receipt.strip():
        raise ToolError("brain_response_invalid", "Brain returned no receipt.", retryable=True)
    event = data.get("event") if isinstance(data.get("event"), dict) else {}
    update = data.get("update") if isinstance(data.get("update"), dict) else {}
    return {
        "ok": True,
        "status": data.get("status"),
        "review_reason": data.get("review_reason"),
        "receipt": receipt,
        "event_id": event.get("id"),
        "update_id": update.get("id"),
    }


def tool_telegram_send_message(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    chat_id = validate_safe_id("chat_id", args.get("chat_id"))
    text = validate_text("text", args.get("text"), required=True)
    reply_to = validate_safe_id("reply_to_message_id", args.get("reply_to_message_id"), required=False)
    message_thread_id = validate_safe_id("message_thread_id", args.get("message_thread_id"), required=False)
    params: dict[str, Any] = {"chat_id": chat_id, "text": trim_telegram_text(text or "")}
    if message_thread_id is not None:
        params["message_thread_id"] = message_thread_id
    if reply_to is not None:
        params["reply_to_message_id"] = reply_to
    result = telegram_api("sendMessage", params, config)
    return {"ok": True, "message_id": result.get("message_id"), "message_thread_id": result.get("message_thread_id")}


def tool_submit_telegram_update(args: dict[str, Any]) -> dict[str, Any]:
    chat_id = validate_safe_id("chat_id", args.get("chat_id"))
    reply_to = validate_safe_id("reply_to_message_id", args.get("reply_to_message_id"), required=False)
    message_thread_id = validate_safe_id("message_thread_id", args.get("message_thread_id"), required=False)
    try:
        intake = tool_brain_submit_intake(args)
        status_note = "\n\nMarked for Brain review." if intake.get("status") == "needs_review" else ""
        send = tool_telegram_send_message(
            {
                "chat_id": chat_id,
                "message_thread_id": message_thread_id,
                "reply_to_message_id": reply_to,
                "text": f"{intake['receipt']}{status_note}",
            }
        )
        return {**intake, "sent": True, "telegram_message_id": send.get("message_id")}
    except Exception as exc:
        error = sanitized_error(exc)
        message = "Brain intake is not available right now. I did not apply this update. Please try again after the gateway or Brain app is healthy."
        try:
            send = tool_telegram_send_message(
                {
                    "chat_id": chat_id,
                    "message_thread_id": message_thread_id,
                    "reply_to_message_id": reply_to,
                    "text": message,
                }
            )
            error["sent"] = True
            error["telegram_message_id"] = send.get("message_id")
        except Exception:
            error["sent"] = False
        return error


TOOLS: dict[str, tuple[str, dict[str, Any], Callable[[dict[str, Any]], dict[str, Any]]]] = {
    "brain_health_check": (
        "Check whether the local Brain app is reachable.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        tool_brain_health_check,
    ),
    "brain_submit_intake": (
        "Submit a text update to Brain Intake and return the Brain receipt without sending Telegram.",
        {
            "type": "object",
            "properties": {
                "raw_text": {"type": "string"},
                "sender_name": {"type": "string"},
                "sender_id": {"type": ["string", "number"]},
                "chat_title": {"type": "string"},
                "message_id": {"type": ["string", "number"]},
                "message_thread_id": {"type": ["string", "number"]},
                "domain_hint": {"type": "string"},
                "project_hint": {"type": "string"},
            },
            "required": ["raw_text"],
            "additionalProperties": False,
        },
        tool_brain_submit_intake,
    ),
    "brain_submit_telegram_update": (
        "Submit a Telegram text update to Brain Intake and send the Brain receipt back to the same chat.",
        {
            "type": "object",
            "properties": {
                "raw_text": {"type": "string"},
                "chat_id": {"type": ["string", "number"]},
                "message_thread_id": {"type": ["string", "number"]},
                "reply_to_message_id": {"type": ["string", "number"]},
                "message_id": {"type": ["string", "number"]},
                "sender_name": {"type": "string"},
                "sender_id": {"type": ["string", "number"]},
                "chat_title": {"type": "string"},
                "domain_hint": {"type": "string"},
                "project_hint": {"type": "string"},
            },
            "required": ["raw_text", "chat_id"],
            "additionalProperties": False,
        },
        tool_submit_telegram_update,
    ),
    "brain_telegram_send_message": (
        "Send a direct Telegram Bot API message. Use only for Brain receipt or text-only unsupported-input notices.",
        {
            "type": "object",
            "properties": {
                "chat_id": {"type": ["string", "number"]},
                "text": {"type": "string"},
                "reply_to_message_id": {"type": ["string", "number"]},
                "message_thread_id": {"type": ["string", "number"]},
            },
            "required": ["chat_id", "text"],
            "additionalProperties": False,
        },
        tool_telegram_send_message,
    ),
}


def rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        return rpc_result(request_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "brain_intake", "version": "0.1.0"}})
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return rpc_result(request_id, {"tools": [{"name": name, "description": desc, "inputSchema": schema} for name, (desc, schema, _) in TOOLS.items()]})
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in TOOLS or not isinstance(args, dict):
            return rpc_error(request_id, -32602, "Invalid tool call.")
        try:
            result = TOOLS[name][2](args)
        except Exception as exc:
            if os.environ.get("BRAIN_INTAKE_DEBUG") == "1":
                traceback.print_exc(file=sys.stderr)
            result = sanitized_error(exc)
        return rpc_result(request_id, {"content": [{"type": "text", "text": json_dumps(result)}], "structuredContent": result, "isError": result.get("ok") is False})
    return rpc_error(request_id, -32601, "Method not found.")


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "call":
        name = sys.argv[2]
        try:
            args = json.loads(sys.stdin.read() or "{}")
            if name not in TOOLS or not isinstance(args, dict):
                raise ToolError("invalid_tool", "Unknown tool or invalid arguments.")
            result = TOOLS[name][2](args)
        except Exception as exc:
            result = sanitized_error(exc)
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        return
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle(request)
        except Exception:
            response = rpc_error(None, -32700, "Parse or internal server error.")
        if response is not None:
            sys.stdout.write(json_dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
