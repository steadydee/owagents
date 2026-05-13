#!/usr/bin/env python3
"""Narrow MCP tools for Owl's Watch receipt intake.

This server intentionally exposes only clerk actions. It reads credentials from
environment/config, never from tool parameters, and always returns structured
JSON-compatible results.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

WORKSPACE = Path(os.environ.get("OWLSWATCH_WORKSPACE", "~/.openclaw/workspace-owlswatch")).expanduser()
CONFIG_PATH = Path(os.environ.get("OPENCLAW_CONFIG_PATH", "~/.openclaw-owlswatch/openclaw.json")).expanduser()
STATE_DIR = Path(os.environ.get("OPENCLAW_STATE_DIR", "~/.openclaw-owlswatch")).expanduser()
SPOOL_INTAKE = WORKSPACE / "spool" / "intake"
SPOOL_GROUPS = WORKSPACE / "spool" / "media-groups"
MEDIA_INBOUND = STATE_DIR / "media" / "inbound"
MEMORY_DIR = WORKSPACE / "memory"
DEFAULT_API_BASE_URL = "https://operations.owlswatch.com"

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,160}$")
TEXT_RE = re.compile(r"^[\s\S]{0,4096}$")


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
    mcp_env = config.get("mcp", {}).get("servers", {}).get("owlswatch_intake", {}).get("env", {})
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


def operations_token(config: dict[str, Any]) -> str:
    token = cfg_env(config, "EXPENSE_INTAKE_API_TOKEN")
    if token:
        return token
    raise ToolError("config_missing", "Operations intake token is missing from tool environment/config.")


def operations_base_url(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "OPERATIONS_API_BASE_URL") or DEFAULT_API_BASE_URL
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("config_invalid", "Operations API base URL must be an https URL.")
    return raw.rstrip("/")


def operations_property_id(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "OPERATIONS_PROPERTY_ID")
    if not raw:
        raw = config.get("operations", {}).get("propertyId")
    if not isinstance(raw, str) or not SAFE_ID_RE.match(raw):
        raise ToolError("config_missing", "Operations property id is missing from tool environment/config.")
    return raw


def first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def set_first_present(target: dict[str, Any], key: str, *values: Any) -> None:
    value = first_present(*values)
    if value is not None:
        target[key] = value


def normalize_submitted_by(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    name = first_present(value.get("name"), value.get("label"), value.get("username"))
    user_id = first_present(value.get("telegramUserId"), value.get("telegram_user_id"), value.get("id"))
    if name and user_id:
        return f"{name} ({user_id})"
    return name or user_id


def normalize_expense_draft_payload(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    # Property ownership lives in the tool layer so the model cannot create
    # drafts under a property the Operations UI does not read.
    normalized.pop("property_id", None)
    normalized["propertyId"] = operations_property_id(config)

    expense = dict(normalized.get("expense") or {})
    set_first_present(
        expense,
        "expense_date",
        expense.get("expense_date"),
        expense.get("expenseDate"),
        expense.get("date"),
    )
    set_first_present(
        expense,
        "vendor_name",
        expense.get("vendor_name"),
        expense.get("vendorName"),
        expense.get("vendor_name_raw"),
        expense.get("vendorNameRaw"),
        expense.get("vendor"),
    )
    set_first_present(expense, "total_amount", expense.get("total_amount"), expense.get("totalAmount"), expense.get("total"))
    set_first_present(expense, "tax_amount", expense.get("tax_amount"), expense.get("taxAmount"))
    set_first_present(expense, "subtotal_amount", expense.get("subtotal_amount"), expense.get("subtotalAmount"))
    set_first_present(expense, "tip_amount", expense.get("tip_amount"), expense.get("tipAmount"))
    set_first_present(
        expense,
        "notes",
        expense.get("notes"),
        expense.get("description"),
        expense.get("business_purpose"),
        expense.get("businessPurpose"),
    )
    normalized["expense"] = expense

    submitted_by = normalize_submitted_by(normalized.get("submittedBy", normalized.get("submitted_by")))
    if submitted_by is not None:
        normalized.pop("submitted_by", None)
        normalized["submittedBy"] = submitted_by

    agent = normalized.get("agent")
    if isinstance(agent, str):
        normalized["agent"] = {"name": agent}
    return normalized


def vision_config(config: dict[str, Any]) -> tuple[str | None, str | None]:
    return (
        cfg_env(config, "OWLSWATCH_VISION_API_KEY") or cfg_env(config, "OPENAI_API_KEY"),
        cfg_env(config, "OWLSWATCH_VISION_MODEL") or "gpt-4o-mini",
    )


def validate_safe_id(name: str, value: Any) -> str:
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


def validate_paths(value: Any) -> list[Path]:
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        raise ToolError("invalid_input", "local_paths must contain 1 to 10 paths.")
    roots = [SPOOL_INTAKE.resolve(), SPOOL_GROUPS.resolve()]
    paths: list[Path] = []
    for item in value:
        if not isinstance(item, str):
            raise ToolError("invalid_input", "Each local path must be a string.")
        path = Path(item).expanduser().resolve()
        if not any(path == root or root in path.parents for root in roots):
            raise ToolError("invalid_input", "Attachment path must be inside the owlswatch spool.")
        if not path.is_file():
            raise ToolError("not_found", "Attachment path was not found.")
        paths.append(path)
    return paths


def validate_openclaw_media_path(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ToolError("invalid_input", "openclaw_media_path must be a string.")
    path = Path(value).expanduser().resolve()
    root = MEDIA_INBOUND.resolve()
    if root not in path.parents:
        raise ToolError("invalid_input", "OpenClaw media path must be inside the inbound media directory.")
    if not path.is_file():
        raise ToolError("not_found", "OpenClaw inbound media file was not found.")
    return path


def http_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 45) -> dict[str, Any]:
    body = json_dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={**headers, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        retryable = 500 <= exc.code <= 599 or exc.code in (408, 409, 425, 429)
        raise ToolError("http_error", f"Upstream request failed with HTTP {exc.code}.", retryable=retryable) from exc
    except urllib.error.URLError as exc:
        raise ToolError("network_error", "Upstream request failed due to a network error.", retryable=True) from exc


def extract_response_text(data: dict[str, Any]) -> str | None:
    text = data.get("output_text")
    if isinstance(text, str) and text.strip():
        return text
    for item in data.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return None


def normalize_receipt_extraction(data: dict[str, Any], fallback_flags: list[str]) -> dict[str, Any]:
    allowed = {
        "vendor_name",
        "expense_date",
        "currency",
        "total_amount",
        "tax_amount",
        "category",
        "confidence",
        "flags",
        "raw_ocr_text",
        "extraction_status",
    }
    result = {key: data.get(key) for key in allowed}
    flags = result.get("flags")
    result["flags"] = flags if isinstance(flags, list) else []
    result["flags"] = [str(flag) for flag in [*result["flags"], *fallback_flags] if str(flag).strip()]
    confidence = result.get("confidence")
    if isinstance(confidence, (int, float)) and 0 < confidence <= 1:
        result["confidence"] = round(confidence * 100)
    elif confidence is None:
        result["confidence"] = 0
    if result.get("extraction_status") not in ("succeeded", "partial", "failed"):
        result["extraction_status"] = "succeeded" if result.get("vendor_name") or result.get("total_amount") else "failed"
    return result


def openai_extract_receipt(api_key: str, model: str, blob_urls: list[str], caption: str | None) -> dict[str, Any]:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "vendor_name": {"type": ["string", "null"]},
            "expense_date": {"type": ["string", "null"], "description": "Receipt date as YYYY-MM-DD if clearly visible."},
            "currency": {"type": ["string", "null"], "description": "ISO currency code, e.g. COP, USD."},
            "total_amount": {"type": ["number", "null"], "description": "Grand total only. No thousands separators."},
            "tax_amount": {"type": ["number", "null"]},
            "category": {"type": ["string", "null"], "description": "Use the user caption as category only if it clearly names one."},
            "confidence": {"type": "number", "minimum": 0, "maximum": 100},
            "flags": {"type": "array", "items": {"type": "string"}},
            "raw_ocr_text": {"type": ["string", "null"]},
            "extraction_status": {"type": "string", "enum": ["succeeded", "partial", "failed"]},
        },
        "required": [
            "vendor_name",
            "expense_date",
            "currency",
            "total_amount",
            "tax_amount",
            "category",
            "confidence",
            "flags",
            "raw_ocr_text",
            "extraction_status",
        ],
    }
    instructions = (
        "Extract receipt fields from the image. Never invent values. "
        "If a field is unclear or absent, return null and add a short flag. "
        "For Colombian receipts, currency is usually COP when pesos are shown. "
        "Return the grand total, not subtotal, tax, points, or payment change. "
        "Preserve raw OCR text in raw_ocr_text when possible."
    )
    content: list[dict[str, Any]] = [
        {"type": "input_text", "text": f"{instructions}\nUser caption: {caption or ''}".strip()}
    ]
    content.extend({"type": "input_image", "image_url": url} for url in blob_urls)
    payload = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "receipt_extraction",
                "strict": True,
                "schema": schema,
            }
        },
    }
    data = http_json("https://api.openai.com/v1/responses", payload, {"Authorization": f"Bearer {api_key}"}, timeout=90)
    text = extract_response_text(data)
    if not text:
        raise ToolError("vision_response_empty", "OpenAI vision returned no structured text.", retryable=True)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolError("vision_response_invalid", "OpenAI vision returned invalid JSON.", retryable=True) from exc
    if not isinstance(parsed, dict):
        raise ToolError("vision_response_invalid", "OpenAI vision returned an invalid JSON shape.", retryable=True)
    return normalize_receipt_extraction(parsed, ["openai_vision"])


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


def tool_telegram_get_file(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    file_id = validate_safe_id("file_id", args.get("file_id"))
    result = telegram_api("getFile", {"file_id": file_id}, config)
    file_path = result.get("file_path")
    if not isinstance(file_path, str):
        raise ToolError("telegram_error", "Telegram getFile returned no file path.")
    mime_type, _ = mimetypes.guess_type(file_path)
    return {"file_path": file_path, "file_size": result.get("file_size"), "mime_type": mime_type}


def tool_telegram_download_file(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    source_message_id = validate_safe_id("source_message_id", args.get("source_message_id"))
    index = args.get("index", 1)
    if not isinstance(index, int) or not 1 <= index <= 20:
        raise ToolError("invalid_input", "index must be an integer from 1 to 20.")

    inbound_path = args.get("openclaw_media_path")
    remote_path: str | None = None
    if inbound_path:
        source_path = validate_openclaw_media_path(inbound_path)
        ext = source_path.suffix.lower() or ".jpg"
    else:
        file_id = validate_safe_id("file_id", args.get("file_id"))
        info = telegram_api("getFile", {"file_id": file_id}, config)
        remote_path = info.get("file_path")
        if not isinstance(remote_path, str):
            raise ToolError("telegram_error", "Telegram getFile returned no file path.")
        ext = Path(remote_path).suffix.lower() or ".jpg"

    if not re.match(r"^\.[a-z0-9]{1,8}$", ext):
        ext = ".bin"
    target_dir = SPOOL_INTAKE / source_message_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (target_dir / f"original-{index}{ext}").resolve()
    if SPOOL_INTAKE.resolve() not in target.parents:
        raise ToolError("invalid_input", "Resolved download path escaped the spool.")

    tmp = target.with_suffix(target.suffix + ".part")
    if inbound_path:
        shutil.copyfile(source_path, tmp)
        data = tmp.read_bytes()
    else:
        token = telegram_token(config)
        url = f"https://api.telegram.org/file/bot{urllib.parse.quote(token)}/{urllib.parse.quote(remote_path or '')}"
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = resp.read()
        except urllib.error.HTTPError as exc:
            raise ToolError("telegram_http_error", f"Telegram file download failed with HTTP {exc.code}.", retryable=500 <= exc.code <= 599) from exc
        except urllib.error.URLError as exc:
            raise ToolError("telegram_network_error", "Telegram file download failed due to a network error.", retryable=True) from exc
        tmp.write_bytes(data)
    tmp.replace(target)
    mime_type, _ = mimetypes.guess_type(target.name)
    return {"local_path": str(target), "bytes_written": len(data), "content_type": mime_type, "sha256": hashlib.sha256(data).hexdigest()}


def album_state_path(chat_id: str, media_group_id: str) -> Path:
    return SPOOL_GROUPS / chat_id / media_group_id / "state.json"


def read_album_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"photos": [], "created_at": now_iso(), "claim_owner": None}
    return json.loads(path.read_text())


def write_album_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)


def tool_album_buffer_store(args: dict[str, Any]) -> dict[str, Any]:
    media_group_id = validate_safe_id("media_group_id", args.get("media_group_id"))
    chat_id = validate_safe_id("chat_id", args.get("chat_id"))
    file_id = validate_safe_id("file_id", args.get("file_id"))
    caption = validate_text("caption_if_present", args.get("caption_if_present"))
    message_id = validate_safe_id("source_message_id", args.get("source_message_id", file_id))
    state_path = album_state_path(chat_id, media_group_id)
    lock_path = state_path.with_suffix(".lock")
    with atomic_lock(lock_path):
        state = read_album_state(state_path)
        photos = state.setdefault("photos", [])
        if not any(p.get("file_id") == file_id for p in photos):
            photos.append({"file_id": file_id, "source_message_id": message_id, "caption_if_present": caption, "arrived_at": now_iso()})
        state["last_arrival_at"] = now_iso()
        write_album_state(state_path, state)
    return {"status": "stored", "photo_count": len(state["photos"]), "last_arrival_at": state["last_arrival_at"]}


class atomic_lock:
    def __init__(self, path: Path, timeout_seconds: float = 10.0):
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.fd: int | None = None

    def __enter__(self):
        deadline = time.monotonic() + self.timeout_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"{os.getpid()} {now_iso()}".encode())
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise ToolError("lock_timeout", "Could not acquire durable album lock.", retryable=True)
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def tool_album_buffer_check(args: dict[str, Any]) -> dict[str, Any]:
    media_group_id = validate_safe_id("media_group_id", args.get("media_group_id"))
    chat_id = validate_safe_id("chat_id", args.get("chat_id"))
    claim_owner = validate_safe_id("claim_owner", args.get("claim_owner", f"pid-{os.getpid()}"))
    quiet_seconds = args.get("quiet_seconds", 5)
    if not isinstance(quiet_seconds, (int, float)) or quiet_seconds < 0 or quiet_seconds > 30:
        raise ToolError("invalid_input", "quiet_seconds must be a number from 0 to 30.")
    state_path = album_state_path(chat_id, media_group_id)
    lock_path = state_path.with_suffix(".lock")
    wait_until_quiet(state_path, quiet_seconds)
    with atomic_lock(lock_path):
        state = read_album_state(state_path)
        last = state.get("last_arrival_at")
        photos = state.get("photos", [])
        complete = is_quiet(last, quiet_seconds) and bool(photos)
        owner = state.get("claim_owner")
        if complete and owner is None:
            state["claim_owner"] = claim_owner
            owner = claim_owner
            write_album_state(state_path, state)
        claimed_by_this_run = owner == claim_owner
    return {
        "complete": bool(complete and claimed_by_this_run),
        "photos": photos if claimed_by_this_run else [],
        "photos_in_album": len(photos),
        "last_arrival_at": last,
        "claim_owner": owner,
    }


def is_quiet(last_arrival_at: Any, quiet_seconds: float) -> bool:
    if not isinstance(last_arrival_at, str):
        return False
    try:
        last_dt = dt.datetime.fromisoformat(last_arrival_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (dt.datetime.now(dt.timezone.utc) - last_dt).total_seconds() >= quiet_seconds


def wait_until_quiet(state_path: Path, quiet_seconds: float) -> None:
    deadline = time.monotonic() + min(quiet_seconds + 1.0, 31.0)
    while time.monotonic() < deadline:
        state = read_album_state(state_path)
        last = state.get("last_arrival_at")
        if not state.get("photos") or state.get("claim_owner") or is_quiet(last, quiet_seconds):
            return
        try:
            last_dt = dt.datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            age = (dt.datetime.now(dt.timezone.utc) - last_dt).total_seconds()
            time.sleep(max(0.05, min(quiet_seconds - age + 0.05, 1.0)))
        except ValueError:
            return


def multipart_upload(url: str, paths: list[Path], token: str, property_id: str) -> dict[str, Any]:
    boundary = "----owlswatch" + hashlib.sha256(str(time.time()).encode()).hexdigest()[:24]
    chunks: list[bytes] = []
    for name in ("propertyId", "property_id"):
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(property_id.encode())
        chunks.append(b"\r\n")
    for path in paths:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="files"; filename="{path.name}"\r\n'.encode())
        chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode())
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise ToolError("operations_upload_http_error", f"Operations upload failed with HTTP {exc.code}.", retryable=500 <= exc.code <= 599) from exc
    except urllib.error.URLError as exc:
        raise ToolError("operations_network_error", "Operations upload failed due to a network error.", retryable=True) from exc


def tool_operations_upload_attachment(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    paths = validate_paths(args.get("local_paths"))
    data = multipart_upload(
        f"{operations_base_url(config)}/api/expenses/attachments/upload",
        paths,
        operations_token(config),
        operations_property_id(config),
    )
    attachments = data.get("attachments") or data.get("files") or data.get("urls")
    if not isinstance(attachments, list):
        attachments = []
        for path in paths:
            attachments.append({"url": None, "fileName": path.name, "contentType": mimetypes.guess_type(path.name)[0], "sizeBytes": path.stat().st_size})
    normalized = []
    for i, item in enumerate(attachments):
        if isinstance(item, str):
            path = paths[min(i, len(paths) - 1)]
            normalized.append({"url": item, "fileName": path.name, "contentType": mimetypes.guess_type(path.name)[0], "sizeBytes": path.stat().st_size})
        elif isinstance(item, dict):
            normalized.append({
                "url": item.get("url"),
                "fileName": item.get("fileName") or item.get("filename") or paths[min(i, len(paths) - 1)].name,
                "contentType": item.get("contentType") or item.get("mime_type") or mimetypes.guess_type(paths[min(i, len(paths) - 1)].name)[0],
                "sizeBytes": item.get("sizeBytes") or item.get("size") or paths[min(i, len(paths) - 1)].stat().st_size,
            })
    return {"attachments": normalized}


def tool_operations_create_expense_draft(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    payload = args.get("payload")
    if not isinstance(payload, dict):
        raise ToolError("invalid_input", "payload must be an object.")
    root_payload = {key: value for key, value in args.items() if key != "payload"}
    if root_payload:
        if payload:
            raise ToolError("invalid_input", "Put all draft fields inside payload; do not pass root-level draft fields.")
        payload = root_payload
    payload = normalize_expense_draft_payload(config, payload)
    idempotency_key = payload.get("idempotencyKey")
    if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 256:
        raise ToolError("invalid_input", "payload.idempotencyKey is required.")
    if not isinstance(payload.get("expense"), dict) or not isinstance(payload.get("attachments"), list):
        raise ToolError("invalid_input", "payload.expense and payload.attachments are required.")
    url = f"{operations_base_url(config)}/api/expenses/intake"
    headers = {"Authorization": f"Bearer {operations_token(config)}"}
    last_error: ToolError | None = None
    for attempt in range(2):
        try:
            data = http_json(url, payload, headers)
            return {
                "ok": bool(data.get("ok", True)),
                "expense_id": data.get("expense_id") or data.get("expenseId") or data.get("id"),
                "status": data.get("status", "draft"),
                "review_url": data.get("review_url") or data.get("reviewUrl"),
            }
        except ToolError as exc:
            last_error = exc
            if not exc.retryable or attempt == 1:
                raise
            time.sleep(1.0)
    raise last_error or ToolError("operations_error", "Operations intake failed.", retryable=False)


def tool_vision_extract_receipt(args: dict[str, Any]) -> dict[str, Any]:
    blob_urls = args.get("blob_urls")
    caption = validate_text("user_caption_if_present", args.get("user_caption_if_present"))
    if not isinstance(blob_urls, list) or not 1 <= len(blob_urls) <= 10 or not all(isinstance(u, str) and u.startswith("https://") for u in blob_urls):
        raise ToolError("invalid_input", "blob_urls must be a non-empty list of https URLs.")
    config = load_config()
    api_key, model = vision_config(config)
    empty = {
        "vendor_name": None,
        "expense_date": None,
        "currency": None,
        "total_amount": None,
        "tax_amount": None,
        "category": None,
        "confidence": 0,
        "flags": ["vision_provider_not_configured" if not api_key else "vision_extraction_unavailable"],
        "raw_ocr_text": None,
        "extraction_status": "failed",
    }
    if not api_key:
        return empty
    endpoint = cfg_env(config, "OWLSWATCH_VISION_ENDPOINT")
    if not endpoint:
        try:
            return openai_extract_receipt(api_key, model or "gpt-4o-mini", blob_urls, caption)
        except ToolError:
            return empty | {"flags": ["openai_vision_request_failed"], "extraction_status": "failed"}
    payload = {"model": model, "blob_urls": blob_urls, "user_caption_if_present": caption, "response_format": "strict_json_receipt_v1"}
    try:
        data = http_json(endpoint, payload, {"Authorization": f"Bearer {api_key}"}, timeout=90)
    except ToolError:
        return empty | {"flags": ["vision_request_failed"], "extraction_status": "failed"}
    allowed = set(empty.keys())
    result = {k: data.get(k) for k in allowed}
    result.setdefault("flags", [])
    result.setdefault("extraction_status", "succeeded")
    if result.get("confidence") is None:
        result["confidence"] = 0
    return result


def tool_telegram_send_message(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    chat_id = validate_safe_id("chat_id", args.get("chat_id"))
    text = validate_text("text", args.get("text"), required=True)
    reply_to = args.get("reply_to_message_id")
    message_thread_id = args.get("message_thread_id")
    params: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if message_thread_id is not None:
        params["message_thread_id"] = validate_safe_id("message_thread_id", message_thread_id)
    if reply_to is not None:
        params["reply_to_message_id"] = validate_safe_id("reply_to_message_id", reply_to)
    result = telegram_api("sendMessage", params, config)
    return {"ok": True, "message_id": result.get("message_id"), "message_thread_id": result.get("message_thread_id")}


def tool_telegram_send_chat_action(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    chat_id = validate_safe_id("chat_id", args.get("chat_id"))
    action = args.get("action") or "typing"
    if action not in {"typing", "upload_photo"}:
        raise ToolError("invalid_input", "action must be typing or upload_photo.")
    params: dict[str, Any] = {"chat_id": chat_id, "action": action}
    message_thread_id = args.get("message_thread_id")
    if message_thread_id is not None:
        params["message_thread_id"] = validate_safe_id("message_thread_id", message_thread_id)
    telegram_api("sendChatAction", params, config)
    return {"ok": True, "action": action}


def tool_memory_log(args: dict[str, Any]) -> dict[str, Any]:
    content = validate_text("content", args.get("content"), required=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    date = dt.datetime.now().strftime("%Y-%m-%d")
    path = MEMORY_DIR / f"{date}.md"
    with path.open("a") as f:
        f.write(f"- {now_iso()} {content.strip()}\n")
    return {"ok": True}


TOOLS: dict[str, tuple[str, dict[str, Any], Callable[[dict[str, Any]], dict[str, Any]]]] = {
    "owlswatch_telegram_get_file": ("Get Telegram file metadata for a receipt photo.", {"type": "object", "properties": {"file_id": {"type": "string"}}, "required": ["file_id"], "additionalProperties": False}, tool_telegram_get_file),
    "owlswatch_telegram_download_file": ("Download a Telegram file or copy an OpenClaw inbound media file into the durable owlswatch spool.", {"type": "object", "properties": {"file_id": {"type": "string"}, "openclaw_media_path": {"type": "string"}, "source_message_id": {"type": ["string", "number"]}, "index": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["source_message_id", "index"], "additionalProperties": False}, tool_telegram_download_file),
    "owlswatch_telegram_send_message": ("Send a direct Telegram Bot API message. For forum topics, include message_thread_id.", {"type": "object", "properties": {"chat_id": {"type": ["string", "number"]}, "text": {"type": "string"}, "reply_to_message_id": {"type": ["string", "number"]}, "message_thread_id": {"type": ["string", "number"]}}, "required": ["chat_id", "text"], "additionalProperties": False}, tool_telegram_send_message),
    "owlswatch_telegram_send_chat_action": ("Show a short Telegram processing indicator such as typing dots. For forum topics, include message_thread_id.", {"type": "object", "properties": {"chat_id": {"type": ["string", "number"]}, "action": {"type": "string", "enum": ["typing", "upload_photo"]}, "message_thread_id": {"type": ["string", "number"]}}, "required": ["chat_id"], "additionalProperties": False}, tool_telegram_send_chat_action),
    "owlswatch_album_buffer_store": ("Store one album photo arrival in durable spool state.", {"type": "object", "properties": {"media_group_id": {"type": "string"}, "chat_id": {"type": ["string", "number"]}, "file_id": {"type": "string"}, "caption_if_present": {"type": ["string", "null"]}, "source_message_id": {"type": ["string", "number"]}}, "required": ["media_group_id", "chat_id", "file_id"], "additionalProperties": False}, tool_album_buffer_store),
    "owlswatch_album_buffer_check": ("Check album quiet period and atomically claim if complete.", {"type": "object", "properties": {"media_group_id": {"type": "string"}, "chat_id": {"type": ["string", "number"]}, "claim_owner": {"type": "string"}, "quiet_seconds": {"type": "number", "minimum": 0, "maximum": 30}}, "required": ["media_group_id", "chat_id"], "additionalProperties": False}, tool_album_buffer_check),
    "owlswatch_operations_upload_attachment": ("Upload spooled receipt photos to Operations intake attachment endpoint.", {"type": "object", "properties": {"local_paths": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10}}, "required": ["local_paths"], "additionalProperties": False}, tool_operations_upload_attachment),
    "owlswatch_operations_create_expense_draft": ("Create an Operations expense draft with idempotency. The tool owns the Operations property id and normalizes common receipt field names; prefer arguments shaped exactly as { payload: { idempotencyKey, source, sourceMessageId, submittedBy, expense, attachments, agent } }.", {"type": "object", "properties": {"payload": {"type": "object", "additionalProperties": True}}, "required": ["payload"], "additionalProperties": True}, tool_operations_create_expense_draft),
    "owlswatch_vision_extract_receipt": ("Extract receipt fields from uploaded blobs using configured vision provider.", {"type": "object", "properties": {"blob_urls": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10}, "user_caption_if_present": {"type": ["string", "null"]}}, "required": ["blob_urls"], "additionalProperties": False}, tool_vision_extract_receipt),
    "owlswatch_memory_log": ("Append one intake summary line to Cuenta memory.", {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"], "additionalProperties": False}, tool_memory_log),
}


def rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        return rpc_result(request_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "owlswatch_intake", "version": "0.1.0"}})
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
            if os.environ.get("OWLSWATCH_DEBUG") == "1":
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
