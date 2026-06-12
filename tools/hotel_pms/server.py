#!/usr/bin/env python3
"""Narrow PMS tools for the Hotel operations agent.

Hotel is a read-only operations clerk. It calls the PMS tool runtime with a
short-lived machine token and sends staff-facing Telegram notifications. It
does not read the database directly and does not send guest messages.
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
from zoneinfo import ZoneInfo

WORKSPACE = Path(os.environ.get("HOTEL_PMS_WORKSPACE", "~/.openclaw/workspace-hotel-ops")).expanduser()
CONFIG_PATH = Path(os.environ.get("OPENCLAW_CONFIG_PATH", "~/.openclaw-hotel/openclaw.json")).expanduser()
MEMORY_DIR = WORKSPACE / "memory"

DEFAULT_PMS_BASE_URL = "https://pms.owlswatch.com"
DEFAULT_PROPERTY_ID = "owlswatch"
DEFAULT_TIMEZONE = "America/Bogota"

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/+\-]{1,300}$")
TEXT_RE = re.compile(r"^[\s\S]{0,50000}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ToolError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat().replace("+00:00", "Z")


def sanitized_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ToolError):
        return {"ok": False, "error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable}}
    if os.environ.get("HOTEL_PMS_DEBUG") == "1":
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
    mcp_env = config.get("mcp", {}).get("servers", {}).get("hotel_pms", {}).get("env", {})
    value = mcp_env.get(key)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    env_vars = config.get("env", {}).get("vars", {})
    value = env_vars.get(key)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    hotel_cfg = config.get("hotel", {})
    camel = key.lower().split("_")
    camel_key = camel[0] + "".join(part.title() for part in camel[1:])
    value = hotel_cfg.get(camel_key)
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
    return value[:max_len]


def validate_safe_id(name: str, value: Any, required: bool = True) -> str | None:
    if value is None:
        if required:
            raise ToolError("invalid_input", f"{name} is required.")
        return None
    text = str(value)
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


def pms_base_url(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "PMS_BASE_URL") or DEFAULT_PMS_BASE_URL
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("config_invalid", "PMS base URL must be an https URL.")
    return raw.rstrip("/")


def pms_agent_secret(config: dict[str, Any]) -> str:
    raw = cfg_file(config, "OW_AGENT_TOKEN_SECRET") or cfg_env(config, "OW_AGENT_TOKEN_SECRET")
    if not raw:
        raise ToolError("config_missing", "PMS machine-token secret is missing from the Hotel tool environment.")
    return raw


def pms_property_id(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "PMS_PROPERTY_ID") or DEFAULT_PROPERTY_ID
    return validate_safe_id("PMS_PROPERTY_ID", raw) or DEFAULT_PROPERTY_ID


def sign_pms_token(config: dict[str, Any]) -> str:
    now = int(time.time())
    payload = {
        "typ": "agent_access",
        "iss": "owhub",
        "aud": "pms",
        "agentId": "hotel",
        "credentialId": "hotel-openclaw-readonly",
        "actorLabel": "Hotel OpenClaw Agent",
        "permissions": ["pms.read"],
        "propertyIds": [pms_property_id(config)],
        "allowedToolClassifications": ["read"],
        "activePropertyId": pms_property_id(config),
        "iat": now,
        "exp": now + 300,
        "jti": hashlib.sha256(f"hotel-{now}-{os.getpid()}".encode()).hexdigest()[:24],
    }
    encoded = b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(pms_agent_secret(config).encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
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


def pms_tool(config: dict[str, Any], tool_name: str, input_payload: dict[str, Any] | None = None) -> Any:
    tool_name = validate_safe_id("tool_name", tool_name) or ""
    response = http_json(
        f"{pms_base_url(config)}/api/tools/{tool_name}",
        input_payload or {},
        {
            "Authorization": f"Bearer {sign_pms_token(config)}",
            "x-ow-request-source": "internal_agent",
            "x-ow-correlation-id": f"hotel-{int(time.time())}-{os.getpid()}",
        },
        timeout=30,
    )
    if response.get("success") is False:
        raise ToolError("pms_error", "PMS tool runtime returned an error.", retryable=False)
    return response.get("data")


def local_date(config: dict[str, Any], offset_days: int = 0) -> str:
    tz_name = cfg_env(config, "HOTEL_TIMEZONE") or DEFAULT_TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    return (dt.datetime.now(tz).date() + dt.timedelta(days=offset_days)).isoformat()


def compact(value: Any, max_len: int = 1200) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None
    return text[:max_len]


def guest_count(reservation: dict[str, Any]) -> int | None:
    adults = reservation.get("adultsCount")
    children = reservation.get("childrenCount")
    total = 0
    found = False
    for value in (adults, children):
        if isinstance(value, (int, float)):
            total += int(value)
            found = True
    return total if found else None


def visit_phrase(reservation: dict[str, Any]) -> str:
    unit = str(reservation.get("unitType") or "").lower()
    notes = " ".join(str(reservation.get(k) or "") for k in ("specialRequests", "internalNotes", "dietaryNotes")).lower()
    nights = int(reservation.get("nights") or 0)
    if "cabin" in unit or "caba" in unit or nights > 0:
        return "staying in the cabins"
    if "bird" in unit or "bird" in notes or "aves" in notes or "tour" in notes:
        return "arriving for a bird tour"
    return "arriving"


def date_part(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) >= 10 and DATE_RE.match(text[:10]):
        return text[:10]
    return None


def normalize_reservation(row: dict[str, Any], detail: dict[str, Any], context: dict[str, Any] | None, movement: str) -> dict[str, Any]:
    reservation = detail.get("reservation") if isinstance(detail.get("reservation"), dict) else detail
    context_reservation = {}
    if isinstance(context, dict):
        context_reservation = context.get("reservation") if isinstance(context.get("reservation"), dict) else {}
    notes = {
        "specialRequests": compact(reservation.get("specialRequests") or context_reservation.get("specialRequests")),
        "dietaryNotes": compact(reservation.get("dietaryNotes") or context_reservation.get("dietaryNotes")),
        "internalNotes": compact(reservation.get("internalNotes") or context_reservation.get("internalNotes")),
        "expectedArrivalTime": compact(reservation.get("expectedArrivalTime")),
        "transportRequested": reservation.get("transportRequested"),
    }
    checklist = context.get("checklist") if isinstance(context, dict) and isinstance(context.get("checklist"), list) else []
    incomplete = [
        {"key": item.get("key"), "label": item.get("label"), "note": item.get("note")}
        for item in checklist
        if isinstance(item, dict) and not item.get("completed")
    ]
    count = guest_count(reservation)
    return {
        "reservationId": reservation.get("reservationId") or reservation.get("id") or row.get("reservationId"),
        "movement": movement,
        "guestName": reservation.get("guestName") or row.get("guestName"),
        "guestCount": count,
        "partyPhrase": f"party of {count}" if count else "party",
        "visitPhrase": visit_phrase(reservation),
        "arrivalDate": reservation.get("arrivalDate") or row.get("arrivalDate"),
        "departureDate": reservation.get("departureDate") or row.get("departureDate"),
        "nights": reservation.get("nights") or row.get("nights"),
        "unitType": reservation.get("unitType") or row.get("unitType"),
        "source": reservation.get("source") or row.get("source"),
        "paymentStatus": reservation.get("paymentStatus") or row.get("paymentStatus"),
        "balanceDue": reservation.get("balanceDue") or row.get("balanceDue"),
        "notes": {key: value for key, value in notes.items() if value not in (None, "", False)},
        "incompleteChecklist": incomplete,
    }


def normalize_arrival(row: dict[str, Any], detail: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    return normalize_reservation(row, detail, context, "arrival")


def enrich_reservations(config: dict[str, Any], rows: list[Any], movement: str) -> list[dict[str, Any]]:
    reservations = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("reservationId"):
            continue
        detail = pms_tool(config, "get_reservation", {"reservationId": row["reservationId"]}) or {}
        context = pms_tool(config, "get_reservation_context", {"reservationId": row["reservationId"]}) or {}
        reservations.append(normalize_reservation(row, detail, context, movement))
    return reservations


def tool_hotel_pms_get_tomorrow_arrivals(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    date = validate_date("date", args.get("date")) or local_date(config, 1)
    rows = pms_tool(config, "list_arrivals", {"date": date}) or []
    arrivals = enrich_reservations(config, rows, "arrival")
    return {"ok": True, "date": date, "timezone": cfg_env(config, "HOTEL_TIMEZONE") or DEFAULT_TIMEZONE, "arrivals": arrivals}


def tool_hotel_pms_get_tomorrow_summary(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    date = validate_date("date", args.get("date")) or local_date(config, 1)
    arrivals_raw = pms_tool(config, "list_arrivals", {"date": date}) or []
    departures_raw = pms_tool(config, "list_departures", {"date": date}) or []
    in_house_raw = pms_tool(config, "list_in_house_guests", {"date": date}) or []
    stayover_raw = [
        row
        for row in in_house_raw
        if isinstance(row, dict)
        and date_part(row.get("arrivalDate")) is not None
        and date_part(row.get("departureDate")) is not None
        and date_part(row.get("arrivalDate")) < date
        and date_part(row.get("departureDate")) > date
    ]
    return {
        "ok": True,
        "date": date,
        "timezone": cfg_env(config, "HOTEL_TIMEZONE") or DEFAULT_TIMEZONE,
        "arrivals": enrich_reservations(config, arrivals_raw, "arrival"),
        "departures": enrich_reservations(config, departures_raw, "departure"),
        "stayovers": enrich_reservations(config, stayover_raw, "stayover"),
    }


def tool_hotel_pms_list_arrivals(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    date = validate_date("date", args.get("date")) or local_date(config, 0)
    return {"ok": True, "date": date, "arrivals": pms_tool(config, "list_arrivals", {"date": date}) or []}


def tool_hotel_pms_list_departures(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    date = validate_date("date", args.get("date")) or local_date(config, 0)
    return {"ok": True, "date": date, "departures": pms_tool(config, "list_departures", {"date": date}) or []}


def tool_hotel_pms_list_in_house(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    date = validate_date("date", args.get("date")) or local_date(config, 0)
    return {"ok": True, "date": date, "inHouse": pms_tool(config, "list_in_house_guests", {"date": date}) or []}


def tool_hotel_pms_find_reservation(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    allowed = {"guestName", "email", "sourceReference", "status"}
    payload = {k: validate_text(k, v, max_len=300) for k, v in args.items() if k in allowed}
    return {"ok": True, "matches": pms_tool(config, "find_reservation", payload) or []}


def tool_hotel_pms_get_reservation_context(args: dict[str, Any]) -> dict[str, Any]:
    reservation_id = validate_safe_id("reservationId", args.get("reservationId"))
    config = load_config()
    return {"ok": True, "context": pms_tool(config, "get_reservation_context", {"reservationId": reservation_id})}


def tool_hotel_pms_get_dashboard_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    return {"ok": True, "snapshot": pms_tool(config, "get_dashboard_snapshot", {})}


def tool_hotel_pms_get_lifecycle_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    return {"ok": True, "snapshot": pms_tool(config, "get_lifecycle_snapshot", {})}


def telegram_token(config: dict[str, Any]) -> str:
    token = cfg_env(config, "HOTEL_TELEGRAM_BOT_TOKEN")
    if token:
        return token
    value = config.get("channels", {}).get("telegram", {}).get("botToken")
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    raise ToolError("config_missing", "Hotel Telegram bot token is missing.")


def default_chat_id(config: dict[str, Any]) -> str:
    chat_id = cfg_env(config, "HOTEL_TELEGRAM_NOTIFY_CHAT_ID")
    if chat_id:
        return chat_id
    raise ToolError("config_missing", "Hotel Telegram notify chat id is missing.")


def default_thread_id(config: dict[str, Any]) -> str | None:
    return cfg_env(config, "HOTEL_TELEGRAM_NOTIFY_THREAD_ID")


def tool_hotel_telegram_send_message(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    text = validate_text("text", args.get("text"), required=True, max_len=3900)
    chat_id = str(args.get("chat_id") or default_chat_id(config))
    payload: dict[str, Any] = {
        "chat_id": chat_id,
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
    return {"ok": True, "message_id": result.get("message_id"), "chat_id": chat_id, "message_thread_id": thread_id}


def tool_hotel_memory_log(args: dict[str, Any]) -> dict[str, Any]:
    content = validate_text("content", args.get("content"), required=True, max_len=2000)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    date = local_date(load_config(), 0)
    path = MEMORY_DIR / f"{date}.md"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"- {now_iso()} - {content}\\n")
    return {"ok": True, "path": str(path)}


TOOLS: dict[str, tuple[str, dict[str, Any], Callable[[dict[str, Any]], dict[str, Any]]]] = {
    "hotel_pms_get_tomorrow_summary": (
        "Return enriched PMS arrivals, departures, and stayovers for tomorrow or a supplied YYYY-MM-DD date.",
        {"type": "object", "properties": {"date": {"type": ["string", "null"]}}, "additionalProperties": False},
        tool_hotel_pms_get_tomorrow_summary,
    ),
    "hotel_pms_get_tomorrow_arrivals": (
        "Return enriched PMS arrivals for tomorrow or a supplied YYYY-MM-DD date.",
        {"type": "object", "properties": {"date": {"type": ["string", "null"]}}, "additionalProperties": False},
        tool_hotel_pms_get_tomorrow_arrivals,
    ),
    "hotel_pms_list_arrivals": (
        "List PMS arrivals for a supplied date or today.",
        {"type": "object", "properties": {"date": {"type": ["string", "null"]}}, "additionalProperties": False},
        tool_hotel_pms_list_arrivals,
    ),
    "hotel_pms_list_departures": (
        "List PMS departures for a supplied date or today.",
        {"type": "object", "properties": {"date": {"type": ["string", "null"]}}, "additionalProperties": False},
        tool_hotel_pms_list_departures,
    ),
    "hotel_pms_list_in_house": (
        "List PMS in-house reservations for a supplied date or today.",
        {"type": "object", "properties": {"date": {"type": ["string", "null"]}}, "additionalProperties": False},
        tool_hotel_pms_list_in_house,
    ),
    "hotel_pms_find_reservation": (
        "Search PMS reservations by guest, email, reference, or status.",
        {
            "type": "object",
            "properties": {
                "guestName": {"type": ["string", "null"]},
                "email": {"type": ["string", "null"]},
                "sourceReference": {"type": ["string", "null"]},
                "status": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        tool_hotel_pms_find_reservation,
    ),
    "hotel_pms_get_reservation_context": (
        "Get read-only PMS reservation context, finance summary, checklist, and invoice metadata.",
        {"type": "object", "properties": {"reservationId": {"type": "string"}}, "required": ["reservationId"], "additionalProperties": False},
        tool_hotel_pms_get_reservation_context,
    ),
    "hotel_pms_get_dashboard_snapshot": (
        "Get the PMS dashboard snapshot.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        tool_hotel_pms_get_dashboard_snapshot,
    ),
    "hotel_pms_get_lifecycle_snapshot": (
        "Get the PMS guest lifecycle snapshot.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        tool_hotel_pms_get_lifecycle_snapshot,
    ),
    "hotel_telegram_send_message": (
        "Send a staff-facing Telegram message through the Hotel bot. Never sends guest messages.",
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
        tool_hotel_telegram_send_message,
    ),
    "hotel_memory_log": (
        "Append one concise Hotel operations memory line.",
        {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"], "additionalProperties": False},
        tool_hotel_memory_log,
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
        result = TOOLS[tool_name][2](args)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps(sanitized_error(exc), ensure_ascii=False))


if __name__ == "__main__":
    main()
