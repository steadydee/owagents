#!/usr/bin/env python3
"""Narrow Registro compliance tools.

Registro is a compliance clerk. It calls PMS and Luna through their app tool
runtimes with short-lived machine tokens. It does not read app databases
directly, does not expose secrets to the model, and does not perform live SIRE
browser submission until the official form map has been reconfirmed.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

WORKSPACE = Path(os.environ.get("REGISTRO_WORKSPACE", "~/.openclaw/workspace-owlswatch-registro")).expanduser()
CONFIG_PATH = Path(os.environ.get("OPENCLAW_CONFIG_PATH", "~/.openclaw-owlswatch/openclaw.json")).expanduser()
MEDIA_DIR = Path(os.environ.get("REGISTRO_MEDIA_SPOOL", str(WORKSPACE / "media"))).expanduser()

DEFAULT_PMS_BASE_URL = "https://pms.owlswatch.com"
DEFAULT_LUNA_BASE_URL = "https://luna.owlswatch.com"
DEFAULT_PROPERTY_ID = "owlswatch"
CHAKRA_BASE = "https://api.chakrahq.com/v1/ext/plugin/whatsapp"
META_API_VERSION = "v21.0"
MAX_MEDIA_BYTES = 10 * 1024 * 1024

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/+\-]{1,300}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TEXT_RE = re.compile(r"^[\s\S]{0,50000}$")
MRZ_LINE_RE = re.compile(r"^[A-Z0-9<]{1,90}$")
LOCAL_OR_TAILNET_HOST_RE = re.compile(r"^(localhost|127\.0\.0\.1|::1|.*\.ts\.net|100\.\d{1,3}\.\d{1,3}\.\d{1,3})$")

REGISTRO_STATUSES = {
    "pending",
    "awaiting_guest",
    "data_submitted",
    "needs_info",
    "validated",
    "complete",
    "cancelled",
}


class ToolError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sanitized_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ToolError):
        return {"ok": False, "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable}}
    if os.environ.get("REGISTRO_DEBUG") == "1":
        return {"ok": False, "error": {"code": "internal_error", "message": str(exc), "retryable": False}}
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
    mcp_env = config.get("mcp", {}).get("servers", {}).get("registro_compliance", {}).get("env", {})
    value = mcp_env.get(key)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    env_vars = config.get("env", {}).get("vars", {})
    value = env_vars.get(key)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    registro_cfg = config.get("registro", {})
    camel = key.lower().split("_")
    camel_key = camel[0] + "".join(part.title() for part in camel[1:])
    value = registro_cfg.get(camel_key)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    return None


def cfg_file(config: dict[str, Any], key: str) -> str | None:
    path = cfg_env(config, f"{key}_FILE")
    if not path:
        return None
    value = Path(path).expanduser().read_text().strip()
    return value or None


def validate_text(name: str, value: Any, required: bool = False, max_len: int = 50000) -> str | None:
    if value is None:
        if required:
            raise ToolError("invalid_input", f"{name} is required.")
        return None
    if not isinstance(value, str) or not TEXT_RE.match(value):
        raise ToolError("invalid_input", f"{name} is malformed.")
    return value.strip()[:max_len]


def validate_safe_id(name: str, value: Any, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise ToolError("invalid_input", f"{name} is required.")
        return None
    text = str(value).strip()
    if not SAFE_ID_RE.match(text):
        raise ToolError("invalid_input", f"{name} is malformed.")
    return text


def validate_date(name: str, value: Any | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not DATE_RE.match(text):
        raise ToolError("invalid_input", f"{name} must be YYYY-MM-DD.")
    try:
        dt.date.fromisoformat(text)
    except ValueError as exc:
        raise ToolError("invalid_input", f"{name} must be a valid date.") from exc
    return text


def validate_url_base(raw: str, label: str) -> str:
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme == "https" and parsed.netloc:
      return raw.rstrip("/")
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
      return raw.rstrip("/")
    raise ToolError("config_invalid", f"{label} must be an https URL, except loopback http in local development.")


def pms_base_url(config: dict[str, Any]) -> str:
    return validate_url_base(cfg_env(config, "PMS_BASE_URL") or DEFAULT_PMS_BASE_URL, "PMS_BASE_URL")


def luna_base_url(config: dict[str, Any]) -> str:
    return validate_url_base(cfg_env(config, "LUNA_BASE_URL") or DEFAULT_LUNA_BASE_URL, "LUNA_BASE_URL")


def agent_secret(config: dict[str, Any]) -> str:
    raw = cfg_file(config, "OW_AGENT_TOKEN_SECRET") or cfg_env(config, "OW_AGENT_TOKEN_SECRET")
    if not raw:
        raise ToolError("config_missing", "OW_AGENT_TOKEN_SECRET is missing from the Registro tool environment.")
    return raw


def property_id(config: dict[str, Any]) -> str:
    return validate_safe_id("PMS_PROPERTY_ID", cfg_env(config, "PMS_PROPERTY_ID") or DEFAULT_PROPERTY_ID) or DEFAULT_PROPERTY_ID


def sign_agent_token(
    config: dict[str, Any],
    *,
    audience: str,
    permissions: list[str],
    allowed_classifications: list[str],
) -> str:
    now = int(time.time())
    active_property_id = property_id(config)
    payload = {
        "typ": "agent_access",
        "iss": "owhub",
        "aud": audience,
        "agentId": "registro",
        "credentialId": "registro-agent",
        "actorLabel": "Registro Compliance Agent",
        "permissions": permissions,
        "propertyIds": [active_property_id],
        "allowedToolClassifications": allowed_classifications,
        "activePropertyId": active_property_id,
        "iat": now,
        "exp": now + 300,
        "jti": hashlib.sha256(f"registro-{audience}-{now}-{os.getpid()}".encode()).hexdigest()[:24],
    }
    encoded = b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(agent_secret(config).encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{b64url(signature)}"


def http_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
            code = body.get("errorCode") or body.get("code")
            msg = body.get("message")
            if code or msg:
                detail = f" {code or 'upstream_error'}: {msg or ''}".strip()
        except Exception:
            detail = ""
        raise ToolError("http_error", f"Upstream request failed with HTTP {exc.code}.{(' ' + detail) if detail else ''}", retryable=500 <= exc.code < 600) from exc
    except urllib.error.URLError as exc:
        raise ToolError("network_error", "Network request failed.", retryable=True) from exc


def http_get(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> tuple[bytes, dict[str, str]]:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read(MAX_MEDIA_BYTES + 1)
            if len(data) > MAX_MEDIA_BYTES:
                raise ToolError("media_too_large", "Media exceeds the configured maximum size.")
            return data, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        raise ToolError("http_error", f"Upstream GET failed with HTTP {exc.code}.", retryable=500 <= exc.code < 600) from exc
    except urllib.error.URLError as exc:
        raise ToolError("network_error", "Network request failed.", retryable=True) from exc


def http_get_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    data, _headers = http_get(url, headers, timeout)
    return json.loads(data.decode("utf-8"))


def pms_tool(config: dict[str, Any], tool_name: str, input_payload: dict[str, Any] | None = None) -> Any:
    tool_name = validate_safe_id("tool_name", tool_name) or ""
    response = http_json(
        f"{pms_base_url(config)}/api/tools/{tool_name}",
        input_payload or {},
        {
            "Authorization": "Bearer " + sign_agent_token(
                config,
                audience="pms",
                permissions=["pms.registro.read", "pms.registro.write"],
                allowed_classifications=["registro"],
            ),
            "x-ow-active-property-id": property_id(config),
            "x-ow-request-source": "internal_agent",
            "x-ow-correlation-id": f"registro-{int(time.time())}-{os.getpid()}",
        },
        timeout=30,
    )
    if response.get("success") is False:
        raise ToolError("pms_error", response.get("message") or "PMS tool runtime returned an error.", retryable=False)
    return response.get("data")


def luna_tool(config: dict[str, Any], tool_name: str, input_payload: dict[str, Any] | None = None) -> Any:
    tool_name = validate_safe_id("tool_name", tool_name) or ""
    response = http_json(
        f"{luna_base_url(config)}/api/tools/{tool_name}",
        {"input": input_payload or {}, "context": {"requestSource": "internal_agent"}},
        {
            "Authorization": "Bearer " + sign_agent_token(
                config,
                audience="luna",
                permissions=["luna.registro.write"],
                allowed_classifications=["registro"],
            ),
            "x-ow-active-property-id": property_id(config),
            "x-ow-request-source": "internal_agent",
            "x-ow-correlation-id": f"registro-luna-{int(time.time())}-{os.getpid()}",
        },
        timeout=30,
    )
    if response.get("success") is False:
        raise ToolError("luna_error", response.get("message") or "Luna tool runtime returned an error.", retryable=False)
    return response.get("data")


def contained_media_path(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    base = MEDIA_DIR.resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ToolError("invalid_path", "Media path must stay inside the Registro media spool.") from exc
    return path


def ensure_local_or_tailnet_url(raw: str) -> str:
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolError("invalid_url", "URL must be http(s).")
    if not LOCAL_OR_TAILNET_HOST_RE.match(parsed.hostname):
        raise ToolError("invalid_url", "Vision URLs must be local or tailnet-only.")
    return raw


def mrz_char_value(char: str) -> int:
    if char == "<":
        return 0
    if "0" <= char <= "9":
        return ord(char) - ord("0")
    if "A" <= char <= "Z":
        return ord(char) - ord("A") + 10
    raise ToolError("invalid_mrz", "MRZ contains unsupported characters.")


def mrz_check_digit(value: str) -> str:
    weights = [7, 3, 1]
    total = sum(mrz_char_value(char) * weights[index % 3] for index, char in enumerate(value))
    return str(total % 10)


def parse_mrz_date(value: str, *, future_ok: bool = False) -> str | None:
    if not re.match(r"^\d{6}$", value):
        return None
    year = int(value[:2])
    month = int(value[2:4])
    day = int(value[4:6])
    current_year = dt.date.today().year % 100
    century = 2000 if future_ok or year <= current_year else 1900
    try:
        parsed = dt.date(century + year, month, day)
    except ValueError:
        return None
    if not future_ok and parsed > dt.date.today():
        parsed = dt.date(parsed.year - 100, month, day)
    return parsed.isoformat()


def parse_td3(lines: list[str]) -> dict[str, Any]:
    line1, line2 = lines
    if len(line1) != 44 or len(line2) != 44:
        raise ToolError("unsupported_mrz", "Only two-line TD3 passport MRZ is supported by this parser.")
    if not line1.startswith("P<"):
        raise ToolError("unsupported_mrz", "Only passport TD3 MRZ is supported by this parser.")

    names = line1[5:44].rstrip("<").split("<<", 1)
    surnames = names[0].replace("<", " ").strip()
    given_names = names[1].replace("<", " ").strip() if len(names) > 1 else ""

    checks = {
        "docNumber": mrz_check_digit(line2[0:9]) == line2[9],
        "birthDate": mrz_check_digit(line2[13:19]) == line2[19],
        "expiryDate": mrz_check_digit(line2[21:27]) == line2[27],
        "personalNumber": mrz_check_digit(line2[28:42]) == line2[42],
        "composite": mrz_check_digit(line2[0:10] + line2[13:20] + line2[21:43]) == line2[43],
    }

    return {
        "docType": "P",
        "docNumber": line2[0:9].replace("<", ""),
        "nationalityIso": line2[10:13].replace("<", ""),
        "fechaNacimiento": parse_mrz_date(line2[13:19]),
        "sexo": line2[20].replace("<", ""),
        "docExpiry": parse_mrz_date(line2[21:27], future_ok=True),
        "primerApellido": surnames.split(" ")[0] if surnames else None,
        "segundoApellido": " ".join(surnames.split(" ")[1:]) or None,
        "nombres": given_names or None,
        "mrzChecksumsOk": all(checks.values()),
        "checks": checks,
    }


def parse_td1(lines: list[str]) -> dict[str, Any]:
    line1, line2, line3 = lines
    if len(line1) != 30 or len(line2) != 30 or len(line3) != 30:
        raise ToolError("unsupported_mrz", "TD1 card MRZ must contain three 30-character lines.")

    names = line3.rstrip("<").split("<<", 1)
    surnames = names[0].replace("<", " ").strip()
    given_names = names[1].replace("<", " ").strip() if len(names) > 1 else ""

    checks = {
        "docNumber": mrz_check_digit(line1[5:14]) == line1[14],
        "birthDate": mrz_check_digit(line2[0:6]) == line2[6],
        "expiryDate": mrz_check_digit(line2[8:14]) == line2[14],
        "composite": mrz_check_digit(line1[5:30] + line2[0:7] + line2[8:15] + line2[18:29]) == line2[29],
    }

    return {
        "docType": line1[0:2].replace("<", "") or None,
        "docNumber": line1[5:14].replace("<", ""),
        "nationalityIso": line2[15:18].replace("<", ""),
        "fechaNacimiento": parse_mrz_date(line2[0:6]),
        "sexo": line2[7].replace("<", ""),
        "docExpiry": parse_mrz_date(line2[8:14], future_ok=True),
        "primerApellido": surnames.split(" ")[0] if surnames else None,
        "segundoApellido": " ".join(surnames.split(" ")[1:]) or None,
        "nombres": given_names or None,
        "mrzChecksumsOk": all(checks.values()),
        "checks": checks,
    }


def normalize_mrz_lines(raw_lines: Any) -> list[str]:
    if isinstance(raw_lines, str):
        lines = raw_lines.splitlines()
    elif isinstance(raw_lines, list):
        lines = [str(line) for line in raw_lines]
    else:
        raise ToolError("invalid_input", "lines must be a string or string array.")
    normalized = [re.sub(r"\s+", "", line).upper() for line in lines if str(line).strip()]
    if len(normalized) not in {2, 3} or not all(MRZ_LINE_RE.match(line) for line in normalized):
        raise ToolError("invalid_mrz", "MRZ must contain TD3 two-line or TD1 three-line alphanumeric/filler data.")
    return normalized


def tool_registro_parse_mrz(args: dict[str, Any]) -> dict[str, Any]:
    lines = normalize_mrz_lines(args.get("lines"))
    parser = parse_td3 if len(lines) == 2 else parse_td1
    return {"ok": True, "document": parser(lines)}


def tool_registro_list_pending(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    limit = int(args.get("limit") or 50)
    return {"ok": True, "result": pms_tool(config, "registro_list_pending", {"limit": max(1, min(limit, 200))})}


def tool_registro_get(args: dict[str, Any]) -> dict[str, Any]:
    registration_id = validate_safe_id("registrationId", args.get("registrationId"))
    config = load_config()
    return {"ok": True, "registration": pms_tool(config, "registro_get", {"registrationId": registration_id})}


def tool_registro_fetch_media(args: dict[str, Any]) -> dict[str, Any]:
    registration_id = validate_safe_id("registrationId", args.get("registrationId"))
    config = load_config()
    registration = pms_tool(config, "registro_get", {"registrationId": registration_id}) or {}
    media_source = registration.get("mediaSource")
    if media_source not in {"whatsapp", "desk"}:
        raise ToolError("media_missing", "Registration does not have an attached media reference yet.")
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    if media_source == "whatsapp" and registration.get("chakraMediaId"):
        fetched = fetch_chakra_media(config, registration_id, str(registration["chakraMediaId"]))
        if fetched:
            return {"ok": True, "media": fetched}
    return {
        "ok": True,
        "media": {
            "registrationId": registration_id,
            "mediaSource": media_source,
            "chakraMediaId": registration.get("chakraMediaId"),
            "deskImageKey": registration.get("deskImageKey"),
            "localPath": None,
            "fetched": False,
            "mode": "reference_only",
        },
    }


def fetch_chakra_media(config: dict[str, Any], registration_id: str, media_id: str) -> dict[str, Any] | None:
    token = cfg_env(config, "CHAKRA_ACCESS_TOKEN")
    plugin_id = cfg_env(config, "CHAKRA_PLUGIN_ID")
    if not token or not plugin_id:
        return None
    media_id = validate_safe_id("chakraMediaId", media_id) or ""
    metadata = http_get_json(
        f"{CHAKRA_BASE}/{urllib.parse.quote(plugin_id)}/api/{META_API_VERSION}/{urllib.parse.quote(media_id)}",
        {"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    media_url = metadata.get("url")
    if not isinstance(media_url, str) or urllib.parse.urlparse(media_url).scheme != "https":
        raise ToolError("media_fetch_failed", "Chakra media metadata did not include a secure media URL.")
    data, headers = http_get(media_url, {"Authorization": f"Bearer {token}"}, timeout=60)
    mime_type = str(metadata.get("mime_type") or headers.get("Content-Type") or "application/octet-stream").split(";")[0]
    ext = {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }.get(mime_type, "bin")
    digest = hashlib.sha256(f"{registration_id}:{media_id}:{time.time()}".encode()).hexdigest()[:16]
    path = (MEDIA_DIR / f"{registration_id}-{digest}.{ext}").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "registrationId": registration_id,
        "mediaSource": "whatsapp",
        "chakraMediaId": media_id,
        "deskImageKey": None,
        "localPath": str(path),
        "mimeType": mime_type,
        "sizeBytes": len(data),
        "fetched": True,
        "mode": "chakra_download",
    }


def tool_registro_delete_media(args: dict[str, Any]) -> dict[str, Any]:
    raw_path = validate_text("localPath", args.get("localPath"), required=True, max_len=1000)
    path = contained_media_path(raw_path or "")
    deleted = False
    if path.exists() and path.is_file():
        path.unlink()
        deleted = True
    return {"ok": True, "deleted": deleted, "localPath": str(path)}


def tool_registro_extract_document_vision(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    endpoint = cfg_env(config, "REGISTRO_VISION_ENDPOINT")
    if not endpoint:
        raise ToolError("config_missing", "REGISTRO_VISION_ENDPOINT is not configured.")
    endpoint = ensure_local_or_tailnet_url(endpoint)
    local_path = args.get("localPath")
    image_url = args.get("imageUrl")
    payload: dict[str, Any] = {}
    if local_path:
        payload["localPath"] = str(contained_media_path(str(local_path)))
    elif image_url:
        payload["imageUrl"] = ensure_local_or_tailnet_url(str(image_url))
    else:
        raise ToolError("invalid_input", "localPath or imageUrl is required.")
    token = cfg_env(config, "REGISTRO_VISION_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return {"ok": True, "extraction": http_json(endpoint, payload, headers, timeout=60)}


def tool_registro_record_extraction(args: dict[str, Any]) -> dict[str, Any]:
    registration_id = validate_safe_id("registrationId", args.get("registrationId"))
    allowed = {
        "docType", "docNumber", "nationalityIso", "nationalityLabel", "primerApellido", "segundoApellido",
        "nombres", "fechaNacimiento", "sexo", "lugarNacimiento", "docExpiry", "sireRequired",
        "extractionMethod", "mrzChecksumsOk", "extractionConfidence", "validationErrors",
    }
    payload = {"registrationId": registration_id}
    for key in allowed:
        if key in args:
            payload[key] = args[key]
    config = load_config()
    return {"ok": True, "registration": pms_tool(config, "registro_record_extraction", payload)}


def tool_registro_set_status(args: dict[str, Any]) -> dict[str, Any]:
    registration_id = validate_safe_id("registrationId", args.get("registrationId"))
    status = validate_safe_id("status", args.get("status"))
    if status not in REGISTRO_STATUSES:
        raise ToolError("invalid_input", "status is not a valid registro status.")
    config = load_config()
    return {"ok": True, "registration": pms_tool(config, "registro_set_status", {"registrationId": registration_id, "status": status})}


def tool_registro_flag_exception(args: dict[str, Any]) -> dict[str, Any]:
    registration_id = validate_safe_id("registrationId", args.get("registrationId"))
    reason = validate_text("reason", args.get("reason"), required=True, max_len=2000)
    config = load_config()
    return {"ok": True, "registration": pms_tool(config, "registro_flag_exception", {"registrationId": registration_id, "reason": reason})}


def tool_registro_record_submission(args: dict[str, Any]) -> dict[str, Any]:
    registration_id = validate_safe_id("registrationId", args.get("registrationId"))
    payload = dict(args)
    payload["registrationId"] = registration_id
    config = load_config()
    return {"ok": True, "submission": pms_tool(config, "registro_record_submission", payload)}


def tool_registro_request_guest_fix(args: dict[str, Any]) -> dict[str, Any]:
    registration_id = validate_safe_id("registrationId", args.get("registrationId"))
    payload = {
        "registrationId": registration_id,
        "reason": validate_text("reason", args.get("reason"), max_len=80) or "needs_fix",
        "message": validate_text("message", args.get("message"), max_len=1000),
    }
    config = load_config()
    return {"ok": True, "result": luna_tool(config, "registro_request_guest_fix", payload)}


def telegram_token(config: dict[str, Any]) -> str:
    token = cfg_env(config, "REGISTRO_TELEGRAM_BOT_TOKEN")
    if token:
        return token
    value = config.get("channels", {}).get("telegram", {}).get("botToken")
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    raise ToolError("config_missing", "Registro Telegram bot token is missing.")


def default_chat_id(config: dict[str, Any]) -> str:
    chat_id = cfg_env(config, "REGISTRO_TELEGRAM_NOTIFY_CHAT_ID")
    if chat_id:
        return chat_id
    raise ToolError("config_missing", "Registro Telegram notify chat id is missing.")


def default_thread_id(config: dict[str, Any]) -> str | None:
    return cfg_env(config, "REGISTRO_TELEGRAM_NOTIFY_THREAD_ID")


def tool_registro_telegram_notify(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    text = validate_text("text", args.get("text"), required=True, max_len=3900)
    payload: dict[str, Any] = {
        "chat_id": str(args.get("chat_id") or default_chat_id(config)),
        "text": text,
        "disable_web_page_preview": True,
    }
    thread_id = args.get("message_thread_id") or default_thread_id(config)
    if thread_id:
        payload["message_thread_id"] = str(thread_id)
    response = http_json(f"https://api.telegram.org/bot{telegram_token(config)}/sendMessage", payload, {}, timeout=20)
    if not response.get("ok"):
        raise ToolError("telegram_error", "Telegram sendMessage failed.", retryable=True)
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    return {"ok": True, "message_id": result.get("message_id"), "chat_id": payload["chat_id"], "message_thread_id": thread_id}


TOOLS: dict[str, tuple[str, dict[str, Any], Callable[[dict[str, Any]], dict[str, Any]]]] = {
    "registro_list_pending": (
        "List PMS registration rows needing extraction or due SIRE work.",
        {"type": "object", "properties": {"limit": {"type": ["integer", "null"]}}, "additionalProperties": False},
        tool_registro_list_pending,
    ),
    "registro_get": (
        "Get one PMS registration with reservation and submission context.",
        {"type": "object", "properties": {"registrationId": {"type": "string"}}, "required": ["registrationId"], "additionalProperties": False},
        tool_registro_get,
    ),
    "registro_fetch_media": (
        "Read the PMS media reference for a registration without exposing image bytes to the model.",
        {"type": "object", "properties": {"registrationId": {"type": "string"}}, "required": ["registrationId"], "additionalProperties": False},
        tool_registro_fetch_media,
    ),
    "registro_parse_mrz": (
        "Parse and checksum a two-line TD3 passport MRZ.",
        {"type": "object", "properties": {"lines": {"type": ["string", "array"]}}, "required": ["lines"], "additionalProperties": False},
        tool_registro_parse_mrz,
    ),
    "registro_extract_document_vision": (
        "Call the configured local or tailnet vision extractor for a contained local path or private URL.",
        {
            "type": "object",
            "properties": {"localPath": {"type": ["string", "null"]}, "imageUrl": {"type": ["string", "null"]}},
            "additionalProperties": False,
        },
        tool_registro_extract_document_vision,
    ),
    "registro_delete_media": (
        "Delete a fetched local media file from the Registro media spool.",
        {"type": "object", "properties": {"localPath": {"type": "string"}}, "required": ["localPath"], "additionalProperties": False},
        tool_registro_delete_media,
    ),
    "registro_record_extraction": (
        "Record extracted document fields and validation errors in PMS.",
        {"type": "object", "properties": {"registrationId": {"type": "string"}}, "required": ["registrationId"], "additionalProperties": True},
        tool_registro_record_extraction,
    ),
    "registro_set_status": (
        "Move a registration through the PMS guarded status machine.",
        {"type": "object", "properties": {"registrationId": {"type": "string"}, "status": {"type": "string"}}, "required": ["registrationId", "status"], "additionalProperties": False},
        tool_registro_set_status,
    ),
    "registro_flag_exception": (
        "Flag a registration exception in PMS.",
        {"type": "object", "properties": {"registrationId": {"type": "string"}, "reason": {"type": "string"}}, "required": ["registrationId", "reason"], "additionalProperties": False},
        tool_registro_flag_exception,
    ),
    "registro_record_submission": (
        "Record a SIRE or TRA submission attempt in PMS.",
        {"type": "object", "properties": {"registrationId": {"type": "string"}}, "required": ["registrationId"], "additionalProperties": True},
        tool_registro_record_submission,
    ),
    "registro_request_guest_fix": (
        "Ask Luna to send one guest correction request inside the WhatsApp service window.",
        {
            "type": "object",
            "properties": {
                "registrationId": {"type": "string"},
                "reason": {"type": ["string", "null"]},
                "message": {"type": ["string", "null"]},
            },
            "required": ["registrationId"],
            "additionalProperties": False,
        },
        tool_registro_request_guest_fix,
    ),
    "registro_telegram_notify": (
        "Send a staff-facing Telegram notification about a registration exception or sweep result.",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "chat_id": {"type": ["string", "number", "null"]},
                "message_thread_id": {"type": ["string", "number", "null"]},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        tool_registro_telegram_notify,
    ),
}


def list_tools() -> dict[str, Any]:
    return {
        "ok": True,
        "tools": [
            {"name": name, "description": description, "parameters": schema}
            for name, (description, schema, _handler) in TOOLS.items()
        ],
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": {"code": "usage", "message": "Usage: server.py list|call <tool>"}}))
        return
    command = sys.argv[1]
    if command == "list":
        print(json.dumps(list_tools(), ensure_ascii=False))
        return
    if command != "call" or len(sys.argv) < 3:
        print(json.dumps({"ok": False, "error": {"code": "usage", "message": "Usage: server.py call <tool>"}}))
        return
    tool_name = sys.argv[2]
    try:
        args = json.loads(sys.stdin.read() or "{}")
        if not isinstance(args, dict):
            raise ToolError("invalid_input", "Tool arguments must be an object.")
        if tool_name not in TOOLS:
            raise ToolError("unknown_tool", f"Unknown tool: {tool_name}")
        print(json.dumps(TOOLS[tool_name][2](args), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps(sanitized_error(exc), ensure_ascii=False))


if __name__ == "__main__":
    main()
