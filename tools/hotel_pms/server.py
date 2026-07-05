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
import html
import html.parser
import json
import os
import re
import subprocess
import sys
import time
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import http.cookiejar
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

WORKSPACE = Path(os.environ.get("HOTEL_PMS_WORKSPACE", "~/.openclaw/workspace-hotel-ops")).expanduser()
CONFIG_PATH = Path(os.environ.get("OPENCLAW_CONFIG_PATH", "~/.openclaw-hotel/openclaw.json")).expanduser()
MEMORY_DIR = WORKSPACE / "memory"
RESERVATION_DRAFT_DIR = WORKSPACE / "spool" / "reservation-drafts"

DEFAULT_PMS_BASE_URL = "https://pms.owlswatch.com"
DEFAULT_PROPERTY_ID = "owlswatch"
DEFAULT_TIMEZONE = "America/Bogota"
LOCAL_OCR_SCRIPT = Path(__file__).with_name("apple_vision_ocr.swift")
DEFAULT_TRA_API_BASE_URL = "https://pms.mincit.gov.co"
DEFAULT_TRA_LOGIN_URL = "https://tra.mincit.gov.co/login/"
DEFAULT_TRA_NEW_GUEST_URL = "https://tra.mincit.gov.co/padd/"
DEFAULT_TRA_REGISTERED_GUESTS_URL = "https://tra.mincit.gov.co/blo"
DEFAULT_SIRE_LOGIN_URL = "https://apps.migracioncolombia.gov.co/sire/public/login.jsf"

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/+\-]{1,300}$")
TEXT_RE = re.compile(r"^[\s\S]{0,50000}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CONFIRMATION_CODE_RE = re.compile(r"^[A-Z0-9]{4,12}$")
PENDING_ID_RE = re.compile(r"^[A-Z0-9]{12,32}$")

PMS_READ_TOOLS = (
    "get_dashboard_snapshot",
    "get_lifecycle_snapshot",
    "list_reservations",
    "get_reservation",
    "find_reservation",
    "get_reservation_context",
    "list_arrivals",
    "list_departures",
    "list_in_house_guests",
    "list_booking_revisions",
    "list_sync_events",
    "get_mapping_status",
    "get_ari_outbox_health",
)

PMS_REGISTRO_READ_TOOLS = (
    "registro_list_pending",
    "registro_get",
    "registro_get_by_reservation",
    "registro_list_guests",
    "registro_list_documents",
    "registro_fetch_document",
    "registro_prepare_government_submission",
)

PMS_REGISTRO_WRITE_TOOLS = (
    "registro_prepare_reservation_documents",
    "registro_record_guest_extraction",
    "registro_record_guest_submission",
    "registro_record_guest_submission_error",
    "registro_mark_guest_needs_review",
    "registro_record_submission",
)

REGISTRO_DOCUMENT_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}

REGISTRO_SUBMISSION_TYPES = ("tra", "sire_entrada", "sire_salida")
REGISTRO_STAFF_RECORDABLE_STATES = ("pending", "failed", "needs_info")
REGISTRO_SUBMIT_MODES = ("dry_run", "submit")

OCR_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


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


def cfg_bool(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = cfg_env(config, key)
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise ToolError("config_invalid", f"{key} must be a boolean.")


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


def validate_confirmation_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not CONFIRMATION_CODE_RE.match(text):
        raise ToolError("invalid_input", "confirmationCode must be the PMS confirmation code, for example A7K2.")
    return text


def validate_pending_id(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not PENDING_ID_RE.match(text):
        raise ToolError("invalid_input", "pendingId is malformed.")
    return text


def normalized_confirmation_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^[\s¡!¿?\"']+|[\s.!¡!¿?\"']+$", "", text)
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch)).strip()


def validate_yes_confirmation(value: Any) -> str:
    normalized = normalized_confirmation_text(value)
    if normalized not in {"si", "s", "yes", "y"}:
        raise ToolError("confirmation_required", "Para crear la reserva, responde exactamente si.")
    return normalized


def validate_int(name: str, value: Any, min_value: int = 0, max_value: int = 100) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ToolError("invalid_input", f"{name} must be a number.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolError("invalid_input", f"{name} must be a number.") from exc
    if parsed < min_value or parsed > max_value:
        raise ToolError("invalid_input", f"{name} must be between {min_value} and {max_value}.")
    return parsed


def validate_bool(name: str, value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if value == "true":
        return True
    if value == "false":
        return False
    raise ToolError("invalid_input", f"{name} must be a boolean.")


def clean_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {key: child for key, child in value.items() if child not in (None, "", [], {})}


def validate_source_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ToolError("invalid_input", "sourceMetadata must be an object.")
    allowed = {
        "telegramChatId",
        "telegramUserId",
        "telegramMessageId",
        "telegramMessageThreadId",
        "telegramUsername",
        "telegramDisplayName",
        "source",
    }
    metadata: dict[str, Any] = {}
    for key, child in value.items():
        if key not in allowed:
            continue
        if isinstance(child, (int, float)):
            metadata[key] = str(int(child))
        elif isinstance(child, str):
            metadata[key] = validate_text(f"sourceMetadata.{key}", child, max_len=300)
        elif child is not None:
            raise ToolError("invalid_input", f"sourceMetadata.{key} is malformed.")
    return clean_payload(metadata)


def merge_source_metadata(*values: Any) -> dict[str, Any] | None:
    merged: dict[str, Any] = {}
    for value in values:
        metadata = validate_source_metadata(value)
        if metadata:
            merged.update(metadata)
    return clean_payload(merged)


def validate_unit_requests(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 6:
        raise ToolError("invalid_input", "unitAllocations must be a short array.")
    output: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ToolError("invalid_input", f"unitAllocations[{index}] must be an object.")
        unit_code = validate_text(f"unitAllocations[{index}].unitCode", item.get("unitCode"), max_len=80)
        if unit_code and unit_code not in ("cabin", "guide-cabin"):
            raise ToolError("invalid_input", "unitAllocations unitCode must be cabin or guide-cabin.")
        quantity = validate_int(f"unitAllocations[{index}].quantity", item.get("quantity"), min_value=1, max_value=10) or 1
        label = validate_text(f"unitAllocations[{index}].label", item.get("label"), max_len=160)
        output.append(clean_payload({"unitCode": unit_code, "quantity": quantity, "label": label}))
    return output


def validate_linked_activities(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 12:
        raise ToolError("invalid_input", "linkedActivities must be a short array.")
    output: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ToolError("invalid_input", f"linkedActivities[{index}] must be an object.")
        booking_type = validate_text(f"linkedActivities[{index}].bookingType", item.get("bookingType"), max_len=40)
        if booking_type not in ("bird_tour", "day_pass"):
            raise ToolError("invalid_input", "linked activity bookingType must be bird_tour or day_pass.")
        output.append(clean_payload({
            "bookingType": booking_type,
            "date": validate_date(f"linkedActivities[{index}].date", item.get("date")),
            "participants": validate_int(f"linkedActivities[{index}].participants", item.get("participants"), min_value=1, max_value=100),
            "notes": validate_text(f"linkedActivities[{index}].notes", item.get("notes"), max_len=600),
        }))
    return output


def validate_prepare_payload(args: dict[str, Any]) -> dict[str, Any]:
    if "rawText" in args or "requestText" in args:
        raise ToolError("invalid_input", "Use sourceText only for original staff text validation.")
    booking_type = validate_text("bookingType", args.get("bookingType"), max_len=40)
    if booking_type and booking_type not in ("overnight_stay", "bird_tour", "day_pass"):
        raise ToolError("invalid_input", "bookingType must be overnight_stay, bird_tour, or day_pass.")
    source = validate_text("source", args.get("source"), max_len=40)
    if source and source not in ("direct", "other"):
        raise ToolError("invalid_input", "Hotel-created reservations may only use direct or other source.")
    commercial_track = validate_text("commercialTrack", args.get("commercialTrack"), max_len=40)
    if commercial_track and commercial_track not in ("direct_guest", "operator"):
        raise ToolError("invalid_input", "commercialTrack must be direct_guest or operator.")
    payer = validate_text("payerResponsibility", args.get("payerResponsibility"), max_len=40)
    if payer and payer not in ("guest", "operator"):
        raise ToolError("invalid_input", "payerResponsibility must be guest or operator.")

    unit_allocations_input = args.get("unitAllocations")
    if unit_allocations_input is None:
        unit_allocations_input = args.get("unitRequests")

    payload = {
        "bookingType": booking_type,
        "guestName": validate_text("guestName", args.get("guestName"), max_len=300),
        "guestEmail": validate_text("guestEmail", args.get("guestEmail"), max_len=300),
        "guestPhone": validate_text("guestPhone", args.get("guestPhone"), max_len=80),
        "operatorName": validate_text("operatorName", args.get("operatorName"), max_len=300),
        "source": source,
        "commercialTrack": commercial_track,
        "payerResponsibility": payer,
        "sourceReference": validate_text("sourceReference", args.get("sourceReference"), max_len=300),
        "arrivalDate": validate_date("arrivalDate", args.get("arrivalDate")),
        "departureDate": validate_date("departureDate", args.get("departureDate")),
        "visitDate": validate_date("visitDate", args.get("visitDate")),
        "adultsCount": validate_int("adultsCount", args.get("adultsCount"), min_value=1, max_value=100),
        "childrenCount": validate_int("childrenCount", args.get("childrenCount"), min_value=0, max_value=100),
        "infantsCount": validate_int("infantsCount", args.get("infantsCount"), min_value=0, max_value=20),
        "unitAllocations": validate_unit_requests(unit_allocations_input),
        "expectedArrivalTime": validate_text("expectedArrivalTime", args.get("expectedArrivalTime"), max_len=80),
        "transportRequested": validate_bool("transportRequested", args.get("transportRequested")),
        "dietaryNotes": validate_text("dietaryNotes", args.get("dietaryNotes"), max_len=1000),
        "specialRequests": validate_text("specialRequests", args.get("specialRequests"), max_len=1000),
        "internalNotes": validate_text("internalNotes", args.get("internalNotes"), max_len=1000),
        "linkedActivities": validate_linked_activities(args.get("linkedActivities")),
        "sourceMetadata": validate_source_metadata(args.get("sourceMetadata")),
    }
    return clean_payload(payload)


def source_text_explicitly_says_one_guest(args: dict[str, Any]) -> bool:
    source_text = validate_text("sourceText", args.get("sourceText"), max_len=1200)
    if not source_text:
        return False
    normalized = unicodedata.normalize("NFKD", source_text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return bool(EXPLICIT_SINGLE_GUEST_RE.search(normalized))


def ambiguous_single_guest_guard(args: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("bookingType") in {"overnight_stay", "bird_tour", "day_pass"} and not any(
        payload.get(key) is not None for key in ("adultsCount", "childrenCount", "infantsCount")
    ):
        return {
            "status": "needs_info",
            "missingFields": ["guest_count"],
            "question": "¿Cuántas personas son?",
            "reason": "guest_count_missing",
        }
    if payload.get("adultsCount") != 1:
        return None
    if source_text_explicitly_says_one_guest(args):
        return None
    return {
        "status": "needs_info",
        "missingFields": ["guest_count"],
        "question": "¿Cuántas personas son?",
        "reason": "guest_count_unclear",
    }


def source_text_date_year_guard(args: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    has_tool_date = any(payload.get(key) for key in ("arrivalDate", "departureDate", "visitDate"))
    has_tool_date = has_tool_date or any(activity.get("date") for activity in payload.get("linkedActivities", []))
    if not has_tool_date:
        return None

    source_text = validate_text("sourceText", args.get("sourceText"), max_len=1200)
    if not source_text:
        return None

    normalized = unicodedata.normalize("NFKD", source_text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))

    if EXPLICIT_YEAR_RE.search(normalized) or RELATIVE_DATE_RE.search(normalized):
        return None

    if MONTH_OR_NUMERIC_DATE_RE.search(normalized):
        return {
            "status": "needs_info",
            "missingFields": ["date_year"],
            "question": "¿De qué año es la reserva?",
            "reason": "date_year_missing",
        }

    return {
        "status": "needs_info",
        "missingFields": ["date"],
        "question": "¿Para qué fecha y año es la reserva?",
        "reason": "date_source_unclear",
    }


def parse_expiry(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ToolError("pms_contract_error", "PMS response did not include expiresAt.")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ToolError("pms_contract_error", "PMS response included malformed expiresAt.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def draft_file_for_code(code: str) -> Path:
    return RESERVATION_DRAFT_DIR / f"{code}.json"


def draft_file_for_pending_id(pending_id: str) -> Path:
    return RESERVATION_DRAFT_DIR / f"pending-{pending_id}.json"


def generate_pending_id(code: str, token: str) -> str:
    entropy = os.urandom(16).hex()
    digest = hashlib.sha256(f"{code}:{token}:{now_iso()}:{entropy}".encode("utf-8")).hexdigest()
    return digest[:16].upper()


def cleanup_expired_reservation_drafts() -> None:
    if not RESERVATION_DRAFT_DIR.exists():
        return
    for path in RESERVATION_DRAFT_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            expires_at = parse_expiry(data.get("expiresAt"))
            if expires_at <= now_utc():
                path.unlink(missing_ok=True)
        except Exception:
            continue


def store_reservation_draft(pms_result: dict[str, Any], request_payload: dict[str, Any]) -> str:
    code = validate_confirmation_code(pms_result.get("confirmationCode"))
    token = pms_result.get("preparedToken")
    if not isinstance(token, str) or not token.strip():
        raise ToolError("pms_contract_error", "PMS ready response did not include preparedToken.")
    expires_at = parse_expiry(pms_result.get("expiresAt"))
    if expires_at <= now_utc():
        raise ToolError("pms_contract_error", "PMS returned an already expired preparedToken.")

    cleanup_expired_reservation_drafts()
    RESERVATION_DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    pending_id = generate_pending_id(code, token)
    payload = {
        "pendingId": pending_id,
        "confirmationCode": code,
        "preparedToken": token,
        "expiresAt": pms_result.get("expiresAt"),
        "summary": staff_safe_value(pms_result.get("summary")),
        "idempotencyKey": pms_result.get("idempotencyKey"),
        "requestPayload": request_payload,
        "sourceMetadata": request_payload.get("sourceMetadata"),
        "createdAt": now_iso(),
        "status": "prepared",
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    draft_file_for_pending_id(pending_id).write_text(text, encoding="utf-8")
    # Keep a code-indexed copy for backwards compatibility with older CREAR <CODE> prompts.
    draft_file_for_code(code).write_text(text, encoding="utf-8")
    return pending_id


def load_reservation_draft(identifier: str, by_pending_id: bool = False) -> dict[str, Any]:
    if by_pending_id:
        pending_id = validate_pending_id(identifier)
        path = draft_file_for_pending_id(pending_id)
    else:
        code = validate_confirmation_code(identifier)
        path = draft_file_for_code(code)
    if not path.exists():
        raise ToolError("prepared_draft_not_found", "No pending reservation draft found for that confirmation.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ToolError("prepared_draft_invalid", "Pending reservation draft is unreadable.") from exc
    expires_at = parse_expiry(data.get("expiresAt"))
    if expires_at <= now_utc():
        path.unlink(missing_ok=True)
        raise ToolError("prepared_draft_expired", "That reservation confirmation code has expired.")
    return data


def mark_reservation_draft_created(draft: dict[str, Any], result: dict[str, Any]) -> None:
    code = draft.get("confirmationCode")
    pending_id = draft.get("pendingId")
    paths: list[Path] = []
    if isinstance(pending_id, str) and PENDING_ID_RE.match(pending_id):
        paths.append(draft_file_for_pending_id(pending_id))
    if isinstance(code, str) and CONFIRMATION_CODE_RE.match(code):
        paths.append(draft_file_for_code(code))
    for path in paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        data["status"] = "created"
        data["createdReservation"] = staff_safe_value(result)
        data["createdAtPms"] = now_iso()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def token_profile(profile: str) -> dict[str, Any]:
    if profile == "prepare":
        return {
            "credential_id": "hotel-openclaw-reservation-prepare",
            "permissions": ["pms.read"],
            "allowed_classifications": ["read", "draft"],
            "allowed_tools": ["agent_prepare_reservation"],
        }
    if profile == "create":
        return {
            "credential_id": "hotel-openclaw-reservation-create",
            "permissions": ["pms.read", "pms.write"],
            "allowed_classifications": ["guarded_write"],
            "allowed_tools": ["agent_create_reservation"],
        }
    if profile == "registro_read":
        return {
            "credential_id": "hotel-openclaw-registro-read",
            "permissions": ["pms.registro.read"],
            "allowed_classifications": ["registro"],
            "allowed_tools": list(PMS_REGISTRO_READ_TOOLS),
        }
    if profile == "registro_write":
        return {
            "credential_id": "hotel-openclaw-registro-write",
            "permissions": ["pms.registro.write"],
            "allowed_classifications": ["registro"],
            "allowed_tools": list(PMS_REGISTRO_WRITE_TOOLS),
        }
    return {
        "credential_id": "hotel-openclaw-readonly",
        "permissions": ["pms.read"],
        "allowed_classifications": ["read"],
        "allowed_tools": list(PMS_READ_TOOLS),
    }


def sign_pms_token(config: dict[str, Any], profile: str = "read") -> str:
    now = int(time.time())
    token = token_profile(profile)
    payload = {
        "typ": "agent_access",
        "iss": "owhub",
        "aud": "pms",
        "agentId": "hotel",
        "credentialId": token["credential_id"],
        "actorLabel": "Hotel OpenClaw Agent",
        "permissions": token["permissions"],
        "propertyIds": [pms_property_id(config)],
        "allowedToolClassifications": token["allowed_classifications"],
        "allowedTools": token["allowed_tools"],
        "activePropertyId": pms_property_id(config),
        "iat": now,
        "exp": now + 300,
        "jti": hashlib.sha256(f"hotel-{profile}-{now}-{os.getpid()}".encode()).hexdigest()[:24],
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


def pms_tool(config: dict[str, Any], tool_name: str, input_payload: dict[str, Any] | None = None, profile: str = "read") -> Any:
    tool_name = validate_safe_id("tool_name", tool_name) or ""
    response = http_json(
        f"{pms_base_url(config)}/api/tools/{tool_name}",
        input_payload or {},
        {
            "Authorization": f"Bearer {sign_pms_token(config, profile)}",
            "x-ow-request-source": "internal_agent",
            "x-ow-correlation-id": f"hotel-{int(time.time())}-{os.getpid()}",
        },
        timeout=30,
    )
    if response.get("success") is False:
        raise ToolError("pms_error", "PMS tool runtime returned an error.", retryable=False)
    return response.get("data")


def extract_response_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    chunks: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") in {"output_text", "text"}:
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    return "".join(chunks).strip()


def registro_vision_api_key(config: dict[str, Any]) -> str | None:
    return (
        cfg_file(config, "REGISTRO_VISION_API_KEY")
        or cfg_file(config, "OPENAI_API_KEY")
        or cfg_env(config, "REGISTRO_VISION_API_KEY")
        or cfg_env(config, "OPENAI_API_KEY")
        or cfg_env(config, "OWLSWATCH_VISION_API_KEY")
    )


def registro_vision_model(config: dict[str, Any]) -> str:
    return cfg_env(config, "REGISTRO_VISION_MODEL") or cfg_env(config, "OWLSWATCH_VISION_MODEL") or "gpt-4o"


def scrub_registro_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, child in value.items():
            lowered = key.lower()
            if any(marker in lowered for marker in ("token", "fetchurl", "url", "base64", "filedata", "bytes")):
                if lowered in {"filesizebytes", "sizebytes"}:
                    safe[key] = child
                continue
            safe[key] = scrub_registro_metadata(child)
        return safe
    if isinstance(value, list):
        return [scrub_registro_metadata(item) for item in value]
    return value


def registration_id_from(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    candidates = [
        value.get("registrationId"),
        value.get("id"),
    ]
    registration = value.get("registration")
    if isinstance(registration, dict):
        candidates.extend([registration.get("registrationId"), registration.get("id")])
    for candidate in candidates:
        if candidate:
            return str(candidate)
    return None


def registration_record_from(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    registration = value.get("registration")
    if isinstance(registration, dict) and registration:
        return registration
    return value


def normalize_submission_type(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "sire": "sire_entrada",
        "sire_entry": "sire_entrada",
        "entrada_sire": "sire_entrada",
        "sireentry": "sire_entrada",
        "sire_exit": "sire_salida",
        "salida_sire": "sire_salida",
        "sireexit": "sire_salida",
    }
    text = aliases.get(text, text)
    if text not in REGISTRO_SUBMISSION_TYPES:
        raise ToolError("invalid_input", "submissionType must be tra, sire_entrada, or sire_salida.")
    return text


def validate_submission_types(value: Any, default: list[str] | None = None) -> list[str]:
    if value in (None, "", []):
        return list(default or [])
    if not isinstance(value, list) or len(value) > len(REGISTRO_SUBMISSION_TYPES):
        raise ToolError("invalid_input", "submissionTypes must be a short array.")
    output: list[str] = []
    for item in value:
        normalized = normalize_submission_type(item)
        if normalized not in output:
            output.append(normalized)
    return output


def validate_submission_state(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == "submitted":
        raise ToolError(
            "live_submission_not_enabled",
            "This tool cannot mark a government submission as submitted without the verified government submitter.",
        )
    if text not in REGISTRO_STAFF_RECORDABLE_STATES:
        raise ToolError("invalid_input", "state must be pending, failed, or needs_info.")
    return text


def due_submission_types_from_registration(value: Any) -> list[str]:
    record = registration_record_from(value)
    raw = record.get("dueSubmissionTypes")
    if not isinstance(raw, list):
        raw = record.get("dueSubmissions")
    if not isinstance(raw, list):
        return []
    output: list[str] = []
    for item in raw:
        try:
            normalized = normalize_submission_type(item)
        except ToolError:
            continue
        if normalized not in output:
            output.append(normalized)
    return output


def safe_guest_submission_label(index: int, guest: dict[str, Any]) -> str:
    role = str(guest.get("role") or "").strip().lower()
    if role == "primary":
        return "guest_primary"
    if role == "companion":
        return f"guest_companion_{index}"
    return f"guest_{index}"


def registro_guest_blockers(guests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if not guests:
        return [{"scope": "registration", "reason": "no_structured_guests"}]
    for index, guest in enumerate(guests, start=1):
        guest_blockers: list[str] = []
        missing = guest.get("missingFields")
        if isinstance(missing, list) and missing:
            guest_blockers.append("missing_fields")
        extraction_status = str(guest.get("extractionStatus") or "").lower()
        if extraction_status and extraction_status not in {"extracted", "validated", "complete"}:
            guest_blockers.append(f"extraction_status:{extraction_status}")
        submission_status = str(guest.get("submissionStatus") or "").lower()
        if submission_status and submission_status != "ready":
            guest_blockers.append(f"submission_status:{submission_status}")
        if not submission_status:
            guest_blockers.append("submission_status_missing")
        if guest_blockers:
            blockers.append({
                "scope": safe_guest_submission_label(index, guest),
                "reasons": guest_blockers,
                "missingFieldCount": len(missing) if isinstance(missing, list) else 0,
            })
    return blockers


def build_registro_submission_plan(
    registration_lookup: dict[str, Any],
    guests: list[dict[str, Any]],
    requested_submission_types: list[str] | None = None,
) -> dict[str, Any]:
    registration = registration_record_from(registration_lookup)
    registration_id = registration_id_from(registration_lookup)
    due_types = due_submission_types_from_registration(registration)
    requested = requested_submission_types or list(due_types)
    blockers: list[dict[str, Any]] = []

    if not registration_id:
        return {
            "status": "blocked",
            "reason": "no_registration",
            "blockers": [{"scope": "registration", "reason": "no_registration"}],
            "dueSubmissionTypes": [],
            "requestedSubmissionTypes": requested,
            "guestCount": 0,
            "readyGuestCount": 0,
            "stagedSubmissions": [],
        }

    registration_status = str(registration.get("status") or "").lower()
    if registration_status == "complete" and not due_types:
        return {
            "status": "no_due",
            "registrationId": registration_id,
            "registrationStatus": registration_status,
            "dueSubmissionTypes": [],
            "requestedSubmissionTypes": requested,
            "guestCount": len(guests),
            "readyGuestCount": len(guests),
            "blockers": [],
            "stagedSubmissions": [],
        }
    if registration_status != "validated":
        blockers.append({"scope": "registration", "reason": f"registration_status:{registration_status or 'unknown'}"})

    if not due_types:
        blockers.append({"scope": "registration", "reason": "no_due_submission_types"})
    for submission_type in requested:
        if submission_type not in due_types:
            blockers.append({"scope": submission_type, "reason": "submission_type_not_due"})

    guest_blockers = registro_guest_blockers(guests)
    blockers.extend(guest_blockers)
    ready_guest_count = max(0, len(guests) - len(guest_blockers))
    status = "ready" if not blockers and requested else "needs_info"
    return {
        "status": status,
        "registrationId": registration_id,
        "registrationStatus": registration_status or None,
        "dueSubmissionTypes": due_types,
        "requestedSubmissionTypes": requested,
        "guestCount": len(guests),
        "readyGuestCount": ready_guest_count,
        "documentCount": registration.get("documentCount"),
        "blockers": blockers,
        "stagedSubmissions": [
            {
                "submissionType": submission_type,
                "state": "ready_to_submit",
                "scope": "registration",
                "guestCount": len(guests),
            }
            for submission_type in requested
            if submission_type in due_types
        ] if status == "ready" else [],
        "warnings": [
            "Live SIRE/TRA submission is not enabled in this Hotel tool yet.",
            "Do not mark submitted until the government system returns a receipt/reference.",
        ],
    }


def guests_from_registro_response(value: Any, registration_id: str | None = None) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("guests", "registrationGuests", "registroGuests"):
        rows = value.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
    registration = value.get("registration")
    if isinstance(registration, dict):
        nested = guests_from_registro_response(registration, registration_id)
        if nested:
            return nested
    # Legacy single-guest PMS shape: treat the registration as the primary guest.
    guest_id = value.get("registrationGuestId") or value.get("guestId") or value.get("id") or registration_id
    if guest_id:
        return [{
            "registrationGuestId": guest_id,
            "role": "primary",
            "displayName": value.get("guestName") or value.get("displayName"),
            "documentType": value.get("documentType"),
            "documentNumber": value.get("documentNumber"),
            "nationality": value.get("nationality"),
            "dateOfBirth": value.get("dateOfBirth"),
            "extractionStatus": value.get("extractionStatus") or value.get("status"),
        }]
    return []


def documents_from_registro_response(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("documents", "registrationDocuments", "files"):
        rows = value.get(key)
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
    return []


def guest_id_from(value: dict[str, Any]) -> str | None:
    for key in ("registrationGuestId", "guestId", "registroGuestId", "id"):
        if value.get(key):
            return str(value[key])
    return None


def document_guest_id(value: dict[str, Any]) -> str | None:
    for key in ("registrationGuestId", "guestId", "registroGuestId", "holderGuestId"):
        if value.get(key):
            return str(value[key])
    return None


def document_id_from(value: dict[str, Any]) -> str | None:
    for key in ("documentId", "id"):
        if value.get(key):
            return str(value[key])
    return None


def document_fetch_token(value: dict[str, Any]) -> str | None:
    for key in ("fetchToken", "documentFetchToken", "token"):
        token = value.get(key)
        if isinstance(token, str) and token:
            return token
    fetch = value.get("fetch")
    if isinstance(fetch, dict):
        token = fetch.get("token") or fetch.get("fetchToken")
        if isinstance(token, str) and token:
            return token
    return None


def fetch_response_base64(value: dict[str, Any]) -> str | None:
    for key in ("base64", "fileBase64", "contentBase64", "dataBase64", "bodyBase64", "fileDataBase64"):
        data = value.get(key)
        if isinstance(data, str) and data:
            return data
    file_value = value.get("file")
    if isinstance(file_value, dict):
        return fetch_response_base64(file_value)
    return None


def fetch_response_content_type(fetch: dict[str, Any], document: dict[str, Any]) -> str | None:
    for source in (fetch, fetch.get("file") if isinstance(fetch.get("file"), dict) else {}, document):
        if not isinstance(source, dict):
            continue
        for key in ("contentType", "mimeType"):
            value = source.get(key)
            if isinstance(value, str) and value:
                return value.lower()
    return None


def normalize_registro_doc_type(value: Any) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKD", str(value).lower())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    if "pass" in normalized or "pasaporte" in normalized:
        return "passport"
    if "cedula" in normalized or "id" in normalized or "identity" in normalized or "card" in normalized:
        return "id_card"
    if "other" in normalized or "otro" in normalized:
        return "other"
    return None


def split_surnames(surname: Any) -> tuple[str | None, str | None]:
    if not isinstance(surname, str):
        return None, None
    parts = [part for part in surname.strip().split() if part]
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def normalize_date_string(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    text = value[:10]
    if DATE_RE.match(text):
        try:
            dt.date.fromisoformat(text)
            return text
        except ValueError:
            return None
    return None


def mrz_char_value(char: str) -> int | None:
    if char.isdigit():
        return int(char)
    if "A" <= char <= "Z":
        return ord(char) - ord("A") + 10
    if char == "<":
        return 0
    return None


def mrz_check_digit(field: str) -> int | None:
    weights = (7, 3, 1)
    total = 0
    for index, char in enumerate(field):
        value = mrz_char_value(char)
        if value is None:
            return None
        total += value * weights[index % len(weights)]
    return total % 10


def mrz_check_ok(field: str, digit: str) -> bool | None:
    if not isinstance(digit, str) or not digit.isdigit():
        return None
    expected = mrz_check_digit(field)
    if expected is None:
        return None
    return expected == int(digit)


def mrz_date(value: str, kind: str) -> str | None:
    if not re.match(r"^\d{6}$", value or ""):
        return None
    yy = int(value[:2])
    month = int(value[2:4])
    day = int(value[4:6])
    year = 2000 + yy
    today = now_utc().date()
    if kind == "birth":
        try:
            candidate = dt.date(year, month, day)
        except ValueError:
            return None
        if candidate > today:
            year -= 100
    elif kind == "expiry" and year < today.year - 5:
        year += 100
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def mrz_clean_name(value: str) -> str | None:
    text = " ".join(part for part in value.replace("<", " ").split() if part)
    return text or None


def usable_mrz_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(part for part in value.strip().split() if part)
    if not text or len(text) > 32:
        return None
    if re.search(r"R{5,}|E{5,}|<{2,}", text):
        return None
    return text


def parse_passport_mrz_line2(line2: str) -> dict[str, Any]:
    if len(line2) < 28:
        return {}
    doc_number_field = line2[:9]
    birth_field = line2[13:19]
    expiry_field = line2[21:27]
    if not re.match(r"^[A-Z0-9<]{9}\d[A-Z<]{3}\d{6}\d[MF<]\d{6}\d", line2[:28]):
        return {}
    checks = [
        value
        for value in (
            mrz_check_ok(doc_number_field, line2[9:10]),
            mrz_check_ok(birth_field, line2[19:20]),
            mrz_check_ok(expiry_field, line2[27:28]),
        )
        if value is not None
    ]
    return {
        "docNumber": doc_number_field.replace("<", "") or None,
        "nationalityIso": line2[10:13].replace("<", "") or None,
        "fechaNacimiento": mrz_date(birth_field, "birth"),
        "sexo": None if line2[20:21] in ("", "<") else line2[20:21],
        "docExpiry": mrz_date(expiry_field, "expiry"),
        "mrzChecksumsOk": all(checks) if checks else None,
    }


def parse_passport_mrz(raw_text: Any) -> dict[str, Any]:
    if not isinstance(raw_text, str) or not raw_text.strip():
        return {}
    candidates: list[str] = []
    for line in raw_text.upper().splitlines():
        compact = re.sub(r"[^A-Z0-9<]", "", line)
        if len(compact) >= 30 and ("<" in compact or parse_passport_mrz_line2(compact[:44])):
            candidates.append(compact)
    for index, line1 in enumerate(candidates):
        if line1.startswith("PE"):
            line1 = "P<" + line1[2:]
        elif line1.startswith("P") and not line1.startswith("P<") and len(line1) > 2:
            line1 = "P<" + line1[2:]
        if not line1.startswith("P<") or index + 1 >= len(candidates):
            continue
        line2 = candidates[index + 1]
        line2_parsed = parse_passport_mrz_line2(line2[:44])
        if not line2_parsed:
            continue
        line1 = line1[:44]
        surname_field, _, given_field = line1[5:].partition("<<")
        return {
            **line2_parsed,
            "primerApellido": mrz_clean_name(surname_field),
            "nombres": mrz_clean_name(given_field),
        }
    for line2 in candidates:
        line2_parsed = parse_passport_mrz_line2(line2[:44])
        if line2_parsed:
            return line2_parsed
    return {}


def parse_ocr_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"\b(\d{1,2})\s+([A-Z]{3})\s+(\d{4})\b", value.upper())
    if not match:
        return normalize_date_string(value)
    month = OCR_MONTHS.get(match.group(2))
    if not month:
        return None
    try:
        return dt.date(int(match.group(3)), month, int(match.group(1))).isoformat()
    except ValueError:
        return None


def next_ocr_value(lines: list[str], label_pattern: str) -> str | None:
    pattern = re.compile(label_pattern, re.I)
    for index, line in enumerate(lines):
        if not pattern.search(line):
            continue
        for candidate in lines[index + 1:index + 5]:
            text = candidate.strip()
            if text and not pattern.search(text):
                return text
    return None


def likely_name_line(value: str) -> bool:
    text = value.strip()
    if not re.match(r"^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ '\\-]{1,40}$", text):
        return False
    blocked = {
        "PASSPORT",
        "UNITED STATES",
        "UNITED STATES OF AMERICA",
        "NATURE",
        "DATE",
        "SEX",
        "USA",
        "MOLDOVA",
        "INDIA",
    }
    upper = text.upper()
    return not any(word in upper for word in blocked)


def ocr_names_from_lines(lines: list[str]) -> tuple[str | None, str | None]:
    surname = next_ocr_value(lines, r"surname|apellido")
    given = next_ocr_value(lines, r"given|pr[eé]nom|nombres|names|naines")
    for index, line in enumerate(lines):
        if re.search(r"given|pr[eé]nom|nombres|names|naines", line, re.I):
            if not surname:
                for previous in reversed(lines[max(0, index - 4):index]):
                    if likely_name_line(previous):
                        surname = previous.strip()
                        break
            if not given:
                for candidate in lines[index + 1:index + 5]:
                    if likely_name_line(candidate):
                        given = candidate.strip()
                        break
    if surname and not likely_name_line(surname):
        surname = None
    if given and not likely_name_line(given):
        given = None
    return surname, given


def split_display_name(display_name: Any) -> tuple[str | None, str | None]:
    if not isinstance(display_name, str) or not display_name.strip():
        return None, None
    parts = [part for part in display_name.strip().split() if part]
    if len(parts) <= 1:
        return display_name.strip(), None
    return " ".join(parts[:-1]), parts[-1]


def apple_vision_ocr_text(content_type: str, encoded_file: str) -> str | None:
    if not LOCAL_OCR_SCRIPT.exists() or not content_type.startswith("image/"):
        return None
    suffix = ".png" if "png" in content_type else ".jpg"
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            tmp_path = Path(handle.name)
            handle.write(base64.b64decode(encoded_file))
        try:
            result = subprocess.run(
                ["/usr/bin/swift", str(LOCAL_OCR_SCRIPT), str(tmp_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=45,
            )
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    lines = payload.get("lines")
    if not isinstance(lines, list):
        return None
    return "\n".join(str(line).strip() for line in lines if str(line).strip()) or None


def local_extract_registro_document(content_type: str, encoded_file: str, guest: dict[str, Any]) -> dict[str, Any] | None:
    raw_text = apple_vision_ocr_text(content_type, encoded_file)
    if not raw_text:
        return None
    lines = raw_text.splitlines()
    mrz = parse_passport_mrz(raw_text)
    ocr_surname, ocr_given = ocr_names_from_lines(lines)
    given_from_guest, surname_from_guest = split_display_name(guest.get("displayName"))
    surname = ocr_surname or usable_mrz_name(mrz.get("primerApellido")) or surname_from_guest
    given_names = ocr_given or usable_mrz_name(mrz.get("nombres")) or given_from_guest
    date_of_birth = mrz.get("fechaNacimiento") or parse_ocr_date(next_ocr_value(lines, r"birth|naissance|nacimiento") or "")
    expiry = mrz.get("docExpiry") or parse_ocr_date(next_ocr_value(lines, r"expiration|expiry|expiraci[oó]n|caducidad") or "")
    issue = parse_ocr_date(next_ocr_value(lines, r"issue|d[eé]livrance|expedici[oó]n") or "")
    nationality = mrz.get("nationalityIso") or ("USA" if "UNITED STATES" in raw_text.upper() else None)
    if not any([mrz.get("docNumber"), date_of_birth, surname, given_names]):
        return None
    return normalize_registro_extraction({
        "documentType": "passport" if mrz or "PASSPORT" in raw_text.upper() else None,
        "documentNumber": mrz.get("docNumber"),
        "nationalityIso": nationality,
        "nationalityLabel": "UNITED STATES OF AMERICA" if nationality == "USA" else None,
        "surname": surname,
        "primerApellido": surname,
        "segundoApellido": None,
        "givenNames": given_names,
        "nombres": given_names,
        "dateOfBirth": date_of_birth,
        "sex": mrz.get("sexo"),
        "documentExpirationDate": expiry,
        "documentIssueDate": issue,
        "documentIssuingCountry": "UNITED STATES OF AMERICA" if nationality == "USA" else None,
        "mrzChecksumsOk": mrz.get("mrzChecksumsOk"),
        "confidence": 85 if mrz.get("docNumber") else 60,
        "flags": [] if mrz.get("docNumber") else ["mrz_not_found"],
        "extractionMethod": "apple_vision_mrz_ocr",
        "rawVisibleText": raw_text,
    })


def normalize_registro_extraction(parsed: dict[str, Any]) -> dict[str, Any]:
    mrz = parse_passport_mrz(parsed.get("rawVisibleText"))
    doc_type = normalize_registro_doc_type(parsed.get("documentType"))
    primer_apellido = parsed.get("primerApellido") or usable_mrz_name(mrz.get("primerApellido"))
    segundo_apellido = parsed.get("segundoApellido")
    if not primer_apellido:
        primer_apellido, segundo_from_surname = split_surnames(parsed.get("surname"))
        segundo_apellido = segundo_apellido or segundo_from_surname
    nombres = parsed.get("givenNames") or parsed.get("nombres") or usable_mrz_name(mrz.get("nombres"))
    display_name = " ".join(str(part).strip() for part in (nombres, primer_apellido, segundo_apellido) if part).strip() or None
    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = 0
    model_confidence = max(0.0, min(1.0, float(confidence) / 100 if confidence > 1 else float(confidence)))
    flags = parsed.get("flags") if isinstance(parsed.get("flags"), list) else []
    warnings = [str(flag)[:160] for flag in flags if str(flag).strip()]
    blocking_flags = {
        "not_identity_document",
        "document_unreadable",
        "no_document_visible",
        "multiple_documents_visible",
        "mrz_checksum_failed",
    }
    errors = [flag for flag in warnings if flag in blocking_flags]
    if isinstance(mrz.get("mrzChecksumsOk"), bool):
        mrz_checksums_ok = mrz.get("mrzChecksumsOk")
    else:
        mrz_checksums_ok = parsed.get("mrzChecksumsOk") if isinstance(parsed.get("mrzChecksumsOk"), bool) else None
    if mrz_checksums_ok is False and "mrz_checksum_failed" not in errors:
        errors.append("mrz_checksum_failed")
    required_checks = {
        "document_number_missing": mrz.get("docNumber") or parsed.get("documentNumber"),
        "name_missing": nombres,
        "surname_missing": primer_apellido,
        "nationality_missing": mrz.get("nationalityIso") or parsed.get("nationalityIso") or parsed.get("nationalityLabel"),
        "birth_date_missing": mrz.get("fechaNacimiento") or parsed.get("dateOfBirth") or parsed.get("fechaNacimiento"),
    }
    for flag, value in required_checks.items():
        if not value and flag not in errors:
            errors.append(flag)
    visible_core_fields = sum(1 for value in required_checks.values() if value)
    optional_fields = [
        doc_type,
        mrz.get("sexo") or parsed.get("sex") or parsed.get("sexo"),
        mrz.get("docExpiry") or parsed.get("documentExpirationDate") or parsed.get("docExpiry"),
        parsed.get("documentIssueDate"),
        parsed.get("documentIssuingCountry"),
    ]
    visible_optional_fields = sum(1 for value in optional_fields if value)
    heuristic_confidence = min(0.95, (visible_core_fields * 0.16) + (visible_optional_fields * 0.04))
    confidence_value = max(model_confidence, heuristic_confidence)
    if confidence_value < 0.65 and errors:
        errors.append("low_confidence")
    nationality_iso = mrz.get("nationalityIso") or parsed.get("nationalityIso")
    sire_required = None
    if isinstance(nationality_iso, str) and nationality_iso:
        sire_required = nationality_iso.strip().upper() not in {"COL", "CO", "COLOMBIA"}
    return {
        "docType": doc_type,
        "docNumber": mrz.get("docNumber") or parsed.get("documentNumber"),
        "nationalityIso": nationality_iso.strip().upper() if isinstance(nationality_iso, str) and nationality_iso else None,
        "nationalityLabel": parsed.get("nationalityLabel"),
        "primerApellido": primer_apellido,
        "segundoApellido": segundo_apellido,
        "nombres": nombres,
        "firstName": nombres,
        "lastName": " ".join(str(part).strip() for part in (primer_apellido, segundo_apellido) if part).strip() or None,
        "displayName": display_name,
        "fechaNacimiento": mrz.get("fechaNacimiento") or normalize_date_string(parsed.get("dateOfBirth") or parsed.get("fechaNacimiento")),
        "sexo": mrz.get("sexo") or parsed.get("sex") or parsed.get("sexo"),
        "docExpiry": mrz.get("docExpiry") or normalize_date_string(parsed.get("documentExpirationDate") or parsed.get("docExpiry")),
        "docIssueDate": normalize_date_string(parsed.get("documentIssueDate")),
        "docIssuingCountry": parsed.get("documentIssuingCountry"),
        "sireRequired": sire_required,
        "extractionMethod": parsed.get("extractionMethod") or "openai_vision_document",
        "mrzChecksumsOk": mrz_checksums_ok,
        "extractionConfidence": confidence_value,
        "validationErrors": errors,
        "warnings": warnings,
        "rawVisibleText": parsed.get("rawVisibleText"),
    }


def openai_extract_registro_document(api_key: str, model: str, content_type: str, encoded_file: str, context: dict[str, Any]) -> dict[str, Any]:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "documentType": {"type": ["string", "null"], "enum": ["passport", "id_card", "other", None]},
            "documentNumber": {"type": ["string", "null"]},
            "nationalityIso": {"type": ["string", "null"], "description": "ISO 3166 nationality/citizenship code if clearly visible or inferable from explicit country text."},
            "nationalityLabel": {"type": ["string", "null"]},
            "surname": {"type": ["string", "null"]},
            "primerApellido": {"type": ["string", "null"]},
            "segundoApellido": {"type": ["string", "null"]},
            "givenNames": {"type": ["string", "null"]},
            "nombres": {"type": ["string", "null"]},
            "dateOfBirth": {"type": ["string", "null"], "description": "YYYY-MM-DD if clearly visible."},
            "sex": {"type": ["string", "null"]},
            "documentExpirationDate": {"type": ["string", "null"], "description": "YYYY-MM-DD if clearly visible."},
            "documentIssueDate": {"type": ["string", "null"], "description": "YYYY-MM-DD if clearly visible."},
            "documentIssuingCountry": {"type": ["string", "null"]},
            "mrzChecksumsOk": {"type": ["boolean", "null"]},
            "confidence": {"type": "number"},
            "flags": {"type": "array", "items": {"type": "string"}},
            "rawVisibleText": {"type": ["string", "null"]},
        },
        "required": [
            "documentType",
            "documentNumber",
            "nationalityIso",
            "nationalityLabel",
            "surname",
            "primerApellido",
            "segundoApellido",
            "givenNames",
            "nombres",
            "dateOfBirth",
            "sex",
            "documentExpirationDate",
            "documentIssueDate",
            "documentIssuingCountry",
            "mrzChecksumsOk",
            "confidence",
            "flags",
            "rawVisibleText",
        ],
    }
    instructions = (
        "Extract legal identity fields from this guest passport or identity-card image for hotel government reporting in Colombia. "
        "Use only what is visible on the document. Never invent missing values. "
        "Return dates as YYYY-MM-DD. Use documentType passport, id_card, or other. "
        "For passports, use MRZ when visible and set mrzChecksumsOk only if you can verify it. "
        "Confidence is 0-100; use high confidence when core fields are clearly readable, not 0. "
        "If the image is unclear, return null fields and flags."
    )
    prompt = {
        "reservationGuestId": context.get("registrationGuestId"),
        "documentId": context.get("documentId"),
        "fileName": context.get("fileName"),
    }
    payload = {
        "model": model,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": f"{instructions}\nContext: {json.dumps(prompt, ensure_ascii=False)}"},
                {"type": "input_image", "image_url": f"data:{content_type};base64,{encoded_file}"},
            ],
        }],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "registro_document_extraction",
                "strict": True,
                "schema": schema,
            }
        },
    }
    data = http_json("https://api.openai.com/v1/responses", payload, {"Authorization": f"Bearer {api_key}"}, timeout=90)
    text = extract_response_text(data)
    if not text:
        raise ToolError("vision_response_empty", "Vision returned no structured text.", retryable=True)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolError("vision_response_invalid", "Vision returned invalid JSON.", retryable=True) from exc
    if not isinstance(parsed, dict):
        raise ToolError("vision_response_invalid", "Vision returned invalid JSON shape.", retryable=True)
    return normalize_registro_extraction(parsed)


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


SENSITIVE_NOTE_MARKERS = (
    "amount",
    "balance",
    "bill",
    "billing",
    "cash",
    "cobro",
    "collect",
    "cop",
    "debt",
    "deposit",
    "finance",
    "factura",
    "invoice",
    "owe",
    "owing",
    "outstanding",
    "paid",
    "pago",
    "pay",
    "payment",
    "precio",
    "price",
    "rate",
    "revenue",
    "saldo",
    "settled",
    "tarifa",
    "total",
    "valor",
)

SENSITIVE_RESULT_KEY_MARKERS = (
    "authorization",
    "payloadhash",
    "preparedtoken",
    "secret",
    "signature",
    "token",
)

CURRENCY_TEXT_RE = re.compile(r"(?i)\bCOP\b|[$]\s*\d")

SENSITIVE_CHECKLIST_MARKERS = (
    "balance",
    "deposit",
    "paid",
    "payment",
    "price",
    "rate",
    "total",
)

ACTIVITY_MARKERS = (
    "activity",
    "bird",
    "aves",
    "tour",
    "pasadia",
    "pasadía",
    "day pass",
)

EXPLICIT_SINGLE_GUEST_RE = re.compile(
    r"(?i)\b(?:1|un|una|one)\s+(?:persona|pax|adulto|adult|cliente|client|huesped|hu[eé]sped|guest|pasajero|passenger)\b|"
    r"\b(?:solo|sola|single|individual)\b"
)
EXPLICIT_YEAR_RE = re.compile(r"\b20\d{2}\b")
MONTH_OR_NUMERIC_DATE_RE = re.compile(
    r"(?i)\b(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"ene|feb|mar|abr|jun|jul|ago|sep|sept|oct|nov|dic|jan|apr|aug|dec)\b|"
    r"\b\d{1,2}\s*[-/]\s*\d{1,2}(?:\s*[-/]\s*\d{2,4})?\b"
)
RELATIVE_DATE_RE = re.compile(
    r"(?i)\b(?:hoy|mañana|manana|pasado\s+mañana|pasado\s+manana|tomorrow|today|"
    r"lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
)


def staff_safe_note(value: Any, max_len: int = 1200) -> str | None:
    text = compact(value, max_len=max_len)
    if not text:
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in SENSITIVE_NOTE_MARKERS):
        return None
    if CURRENCY_TEXT_RE.search(text):
        return None
    return text


def staff_safe_value(value: Any) -> Any:
    """Recursively redact finance-sensitive fields from broad PMS snapshots."""
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key).lower()
            normalized_key = re.sub(r"[^a-z0-9]", "", key_text)
            if any(marker in normalized_key for marker in SENSITIVE_RESULT_KEY_MARKERS):
                continue
            if any(marker in key_text for marker in SENSITIVE_NOTE_MARKERS):
                continue
            safe_child = staff_safe_value(child)
            if safe_child in (None, "", [], {}):
                continue
            output[key] = safe_child
        return output
    if isinstance(value, list):
        output = []
        for child in value:
            safe_child = staff_safe_value(child)
            if safe_child not in (None, "", [], {}):
                output.append(safe_child)
        return output
    if isinstance(value, str):
        return staff_safe_note(value, max_len=2000)
    return value


def staff_safe_checklist_item(item: dict[str, Any]) -> dict[str, Any] | None:
    key = str(item.get("key") or "").lower()
    label = str(item.get("label") or "").lower()
    note = str(item.get("note") or "").lower()
    joined = " ".join([key, label, note])
    if any(marker in joined for marker in SENSITIVE_CHECKLIST_MARKERS):
        return None
    return {"key": item.get("key"), "label": item.get("label"), "note": item.get("note")}


def activity_label(description: Any) -> str | None:
    text = compact(description, max_len=160)
    if not text:
        return None
    text = text.replace("—", "-").replace("–", "-")
    text = re.sub(r"\s*\((?:per person|por persona)\)\s*", "", text, flags=re.I).strip()
    lowered = text.lower()
    if any(marker in lowered for marker in SENSITIVE_NOTE_MARKERS):
        return None
    return text


def operational_activities(context: dict[str, Any] | None, target_date: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(context, dict) or not isinstance(context.get("charges"), list):
        return []
    activities: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str, int | None]] = set()
    for charge in context["charges"]:
        if not isinstance(charge, dict):
            continue
        charge_date = date_part(charge.get("chargeDate"))
        if target_date and charge_date != target_date:
            continue
        category = str(charge.get("category") or "").lower()
        description = str(charge.get("description") or "")
        joined = f"{category} {description}".lower()
        if not any(marker in joined for marker in ACTIVITY_MARKERS):
            continue
        label = activity_label(description)
        if not label:
            continue
        quantity = charge.get("quantity")
        safe_quantity = int(quantity) if isinstance(quantity, (int, float)) and quantity > 0 else None
        key = (charge_date, label, safe_quantity)
        if key in seen:
            continue
        seen.add(key)
        activity = {"date": charge_date, "description": label}
        if safe_quantity is not None:
            activity["quantity"] = safe_quantity
        activities.append(activity)
    return activities


def safe_reservation_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Return staff-safe reservation fields without pricing or payment data."""
    allowed = (
        "reservationId",
        "guestName",
        "guestEmail",
        "source",
        "sourceLabel",
        "status",
        "stage",
        "stageLabel",
        "bookingType",
        "arrivalDate",
        "departureDate",
        "nights",
        "adultsCount",
        "childrenCount",
        "unitType",
        "unitCode",
        "unitName",
        "unitSummary",
        "unitAllocations",
    )
    return {key: row.get(key) for key in allowed if key in row}


def safe_context_bundle(context: dict[str, Any] | None) -> dict[str, Any]:
    """Reduce PMS reservation context to operational data only."""
    if not isinstance(context, dict):
        return {}
    reservation = context.get("reservation") if isinstance(context.get("reservation"), dict) else {}
    notes = {
        "specialRequests": staff_safe_note(reservation.get("specialRequests")),
        "dietaryNotes": staff_safe_note(reservation.get("dietaryNotes")),
        "internalNotes": staff_safe_note(reservation.get("internalNotes")),
    }
    checklist = context.get("checklist") if isinstance(context.get("checklist"), list) else []
    incomplete = []
    completed = []
    for item in checklist:
        if not isinstance(item, dict):
            continue
        safe_item = staff_safe_checklist_item(item)
        if not safe_item:
            continue
        if item.get("completed"):
            completed.append(safe_item)
        else:
            incomplete.append(safe_item)
    return {
        "reservation": safe_reservation_summary(reservation),
        "notes": {key: value for key, value in notes.items() if value},
        "operationalActivities": operational_activities(context),
        "incompleteChecklist": incomplete,
        "completedChecklist": completed,
    }


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


def unit_text(reservation: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("unitType", "unitCode", "unitName", "unitSummary"):
        value = reservation.get(key)
        if value not in (None, ""):
            values.append(str(value))
    allocations = reservation.get("unitAllocations")
    if isinstance(allocations, list):
        for allocation in allocations:
            if not isinstance(allocation, dict):
                continue
            for key in ("unitType", "unitCode", "unitName", "label"):
                value = allocation.get(key)
                if value not in (None, ""):
                    values.append(str(value))
    return " ".join(values).lower()


def booking_category(reservation: dict[str, Any]) -> str:
    booking_type = str(reservation.get("bookingType") or "").lower()
    unit = unit_text(reservation)
    notes = " ".join(str(reservation.get(k) or "") for k in ("specialRequests", "internalNotes", "dietaryNotes")).lower()
    if booking_type == "day_pass":
        return "day_pass"
    if booking_type == "bird_tour":
        return "bird_tour"
    if booking_type == "overnight_stay":
        if "guide-cabin" in unit or "guide cabin" in unit or "guide room" in unit or "habitacion de guia" in unit or "habitación de guía" in unit:
            if "cabin" in unit.replace("guide-cabin", "").replace("guide cabin", "") or "caba" in unit:
                return "cabin"
            return "guide_room"
        if "cabin" in unit or "caba" in unit:
            return "cabin"
        return "overnight_unassigned"
    if booking_type == "guide_room":
        return "guide_room"
    if "bird" in unit or "bird" in notes or "aves" in notes or "tour" in notes:
        return "bird_tour"
    if "day pass" in notes or "pasadia" in notes or "pasadía" in notes:
        return "day_pass"
    if "cabin" in unit or "caba" in unit:
        return "cabin"
    return "unknown"


SAME_DAY_ACTIVITY_CATEGORIES = {"day_pass", "bird_tour"}


def is_same_day_activity(reservation: dict[str, Any]) -> bool:
    return booking_category(reservation) in SAME_DAY_ACTIVITY_CATEGORIES


def filter_lodging_movements(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if isinstance(row, dict) and not is_same_day_activity(row)
    ]


def visit_phrase(reservation: dict[str, Any]) -> str:
    category = booking_category(reservation)
    if category == "cabin":
        return "cabañas"
    if category == "day_pass":
        return "pasadía"
    if category == "bird_tour":
        return "tour de aves"
    if category == "guide_room":
        return "habitación de guía"
    if category == "overnight_unassigned":
        return "reserva de noche sin unidad asignada"
    return "reserva sin tipo definido"


def date_part(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) >= 10 and DATE_RE.match(text[:10]):
        return text[:10]
    return None


def normalize_reservation(row: dict[str, Any], detail: dict[str, Any], context: dict[str, Any] | None, movement: str, activity_date: str | None = None) -> dict[str, Any]:
    reservation = detail.get("reservation") if isinstance(detail.get("reservation"), dict) else detail
    context_reservation = {}
    if isinstance(context, dict):
        context_reservation = context.get("reservation") if isinstance(context.get("reservation"), dict) else {}
    classification = {**row, **context_reservation, **reservation}
    notes = {
        "specialRequests": staff_safe_note(reservation.get("specialRequests") or context_reservation.get("specialRequests")),
        "dietaryNotes": staff_safe_note(reservation.get("dietaryNotes") or context_reservation.get("dietaryNotes")),
        "internalNotes": staff_safe_note(reservation.get("internalNotes") or context_reservation.get("internalNotes")),
        "transportRequested": reservation.get("transportRequested"),
    }
    if movement == "arrival":
        notes["expectedArrivalTime"] = compact(reservation.get("expectedArrivalTime"))
    checklist = context.get("checklist") if isinstance(context, dict) and isinstance(context.get("checklist"), list) else []
    incomplete = []
    for item in checklist:
        if not isinstance(item, dict) or item.get("completed"):
            continue
        safe_item = staff_safe_checklist_item(item)
        if safe_item:
            incomplete.append(safe_item)
    count = guest_count(classification)
    return {
        "reservationId": reservation.get("reservationId") or reservation.get("id") or row.get("reservationId"),
        "movement": movement,
        "guestName": reservation.get("guestName") or row.get("guestName"),
        "guestCount": count,
        "partyPhrase": f"party of {count}" if count else "party",
        "bookingType": classification.get("bookingType"),
        "bookingCategory": booking_category(classification),
        "visitPhrase": visit_phrase(classification),
        "arrivalDate": reservation.get("arrivalDate") or row.get("arrivalDate"),
        "departureDate": reservation.get("departureDate") or row.get("departureDate"),
        "nights": reservation.get("nights") or row.get("nights"),
        "unitType": reservation.get("unitType") or row.get("unitType"),
        "unitCode": classification.get("unitCode"),
        "unitName": classification.get("unitName"),
        "unitSummary": classification.get("unitSummary"),
        "unitAllocations": classification.get("unitAllocations"),
        "source": reservation.get("source") or row.get("source"),
        "operationalActivities": operational_activities(context, activity_date),
        "notes": {key: value for key, value in notes.items() if value not in (None, "", False)},
        "incompleteChecklist": incomplete,
    }


def normalize_arrival(row: dict[str, Any], detail: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    return normalize_reservation(row, detail, context, "arrival")


def enrich_reservations(config: dict[str, Any], rows: list[Any], movement: str, activity_date: str | None = None) -> list[dict[str, Any]]:
    reservations = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("reservationId"):
            continue
        detail = pms_tool(config, "get_reservation", {"reservationId": row["reservationId"]}) or {}
        context = pms_tool(config, "get_reservation_context", {"reservationId": row["reservationId"]}) or {}
        reservations.append(normalize_reservation(row, detail, context, movement, activity_date))
    return reservations


def tool_hotel_pms_get_tomorrow_arrivals(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    date = validate_date("date", args.get("date")) or local_date(config, 1)
    rows = pms_tool(config, "list_arrivals", {"date": date}) or []
    arrivals = enrich_reservations(config, rows, "arrival", date)
    return {"ok": True, "date": date, "timezone": cfg_env(config, "HOTEL_TIMEZONE") or DEFAULT_TIMEZONE, "arrivals": arrivals}


def tool_hotel_pms_get_tomorrow_summary(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    date = validate_date("date", args.get("date")) or local_date(config, 1)
    arrivals_raw = pms_tool(config, "list_arrivals", {"date": date}) or []
    departures_raw = filter_lodging_movements(pms_tool(config, "list_departures", {"date": date}) or [])
    in_house_raw = pms_tool(config, "list_in_house_guests", {"date": date}) or []
    stayover_raw = [
        row
        for row in in_house_raw
        if isinstance(row, dict)
        and not is_same_day_activity(row)
        and date_part(row.get("arrivalDate")) is not None
        and date_part(row.get("departureDate")) is not None
        and date_part(row.get("arrivalDate")) < date
        and date_part(row.get("departureDate")) > date
    ]
    return {
        "ok": True,
        "date": date,
        "timezone": cfg_env(config, "HOTEL_TIMEZONE") or DEFAULT_TIMEZONE,
        "arrivals": enrich_reservations(config, arrivals_raw, "arrival", date),
        "departures": enrich_reservations(config, departures_raw, "departure", date),
        "stayovers": enrich_reservations(config, stayover_raw, "stayover", date),
    }


def tool_hotel_pms_list_arrivals(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    date = validate_date("date", args.get("date")) or local_date(config, 0)
    rows = pms_tool(config, "list_arrivals", {"date": date}) or []
    return {"ok": True, "date": date, "arrivals": [safe_reservation_summary(row) for row in rows if isinstance(row, dict)]}


def tool_hotel_pms_list_departures(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    date = validate_date("date", args.get("date")) or local_date(config, 0)
    rows = filter_lodging_movements(pms_tool(config, "list_departures", {"date": date}) or [])
    return {"ok": True, "date": date, "departures": [safe_reservation_summary(row) for row in rows if isinstance(row, dict)]}


def tool_hotel_pms_list_in_house(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    date = validate_date("date", args.get("date")) or local_date(config, 0)
    rows = pms_tool(config, "list_in_house_guests", {"date": date}) or []
    return {"ok": True, "date": date, "inHouse": [safe_reservation_summary(row) for row in rows if isinstance(row, dict)]}


def tool_hotel_pms_list_reservations(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    allowed = {"search", "source", "status", "dateFrom", "dateTo", "limit"}
    payload: dict[str, Any] = {}
    for key in allowed:
        value = args.get(key)
        if key in {"dateFrom", "dateTo"}:
            payload[key] = validate_date(key, value) if value else None
        elif key == "limit":
            if value is not None:
                try:
                    payload[key] = min(max(int(value), 1), 50)
                except (TypeError, ValueError) as exc:
                    raise ToolError("invalid_input", "limit must be a number.") from exc
        else:
            payload[key] = validate_text(key, value, max_len=300) if value is not None else None
    payload = {key: value for key, value in payload.items() if value not in (None, "")}
    rows = pms_tool(config, "list_reservations", payload) or []
    return {"ok": True, "filters": payload, "reservations": [safe_reservation_summary(row) for row in rows if isinstance(row, dict)]}


def tool_hotel_pms_find_reservation(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    allowed = {"guestName", "email", "sourceReference", "status"}
    payload = {k: validate_text(k, v, max_len=300) for k, v in args.items() if k in allowed}
    rows = pms_tool(config, "find_reservation", payload) or []
    return {"ok": True, "matches": [safe_reservation_summary(row) for row in rows if isinstance(row, dict)]}


def tool_hotel_pms_get_reservation_context(args: dict[str, Any]) -> dict[str, Any]:
    reservation_id = validate_safe_id("reservationId", args.get("reservationId"))
    config = load_config()
    context = pms_tool(config, "get_reservation_context", {"reservationId": reservation_id})
    return {"ok": True, "context": safe_context_bundle(context)}


def tool_hotel_pms_get_dashboard_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    return {"ok": True, "snapshot": staff_safe_value(pms_tool(config, "get_dashboard_snapshot", {}))}


def tool_hotel_pms_get_lifecycle_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    return {"ok": True, "snapshot": staff_safe_value(pms_tool(config, "get_lifecycle_snapshot", {}))}


def tool_hotel_pms_list_booking_revisions(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    allowed = {"processingStatus", "ackStatus"}
    payload = {k: validate_text(k, v, max_len=80) for k, v in args.items() if k in allowed and v is not None}
    return {"ok": True, "revisions": staff_safe_value(pms_tool(config, "list_booking_revisions", payload) or [])}


def tool_hotel_pms_list_sync_events(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    allowed = {"status", "direction", "resourceType"}
    payload = {k: validate_text(k, v, max_len=80) for k, v in args.items() if k in allowed and v is not None}
    return {"ok": True, "events": staff_safe_value(pms_tool(config, "list_sync_events", payload) or [])}


def tool_hotel_pms_get_mapping_status(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    payload = {}
    entity_type = validate_text("entityType", args.get("entityType"), max_len=80) if args.get("entityType") is not None else None
    if entity_type:
        payload["entityType"] = entity_type
    return {"ok": True, "status": staff_safe_value(pms_tool(config, "get_mapping_status", payload))}


def tool_hotel_pms_get_ari_outbox_health(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    return {"ok": True, "health": pms_tool(config, "get_ari_outbox_health", {})}


def tool_hotel_pms_prepare_reservation(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    payload = validate_prepare_payload(args)
    date_guard = source_text_date_year_guard(args, payload)
    if date_guard:
        return {"ok": True, "prepare": date_guard}
    count_guard = ambiguous_single_guest_guard(args, payload)
    if count_guard:
        return {"ok": True, "prepare": count_guard}
    result = pms_tool(config, "agent_prepare_reservation", payload, profile="prepare")
    if not isinstance(result, dict):
        raise ToolError("pms_contract_error", "PMS prepare response was malformed.")

    status = result.get("status")
    safe_result = staff_safe_value(result)
    if status == "ready":
        pending_id = store_reservation_draft(result, payload)
        if not isinstance(safe_result, dict):
            safe_result = {}
        safe_result.pop("confirmationCode", None)
        safe_result["pendingId"] = pending_id
        safe_result["confirmationRequired"] = True
        safe_result["instruction"] = "Responde si para confirmar y crearla en PMS."
    return {"ok": True, "prepare": safe_result}


def tool_hotel_pms_create_reservation(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    pending_id_raw = args.get("pendingId")
    confirmation_code_raw = args.get("confirmationCode")
    if pending_id_raw is not None:
        pending_id = validate_pending_id(pending_id_raw)
        validate_yes_confirmation(args.get("confirmationText"))
        draft = load_reservation_draft(pending_id, by_pending_id=True)
        code = validate_confirmation_code(draft.get("confirmationCode"))
    elif confirmation_code_raw is not None:
        code = validate_confirmation_code(confirmation_code_raw)
        draft = load_reservation_draft(code)
    else:
        raise ToolError("invalid_input", "pendingId or confirmationCode is required.")
    prepared_token = draft.get("preparedToken")
    if not isinstance(prepared_token, str) or not prepared_token.strip():
        raise ToolError("prepared_draft_invalid", "Pending reservation draft is missing its PMS token.")

    source_metadata = merge_source_metadata(draft.get("sourceMetadata"), args.get("sourceMetadata"))
    payload = clean_payload({
        "preparedToken": prepared_token,
        "confirmationCode": code,
        "idempotencyKey": validate_safe_id("idempotencyKey", args.get("idempotencyKey"), required=False) or draft.get("idempotencyKey"),
        "sourceMetadata": source_metadata,
    })
    result = pms_tool(config, "agent_create_reservation", payload, profile="create")
    if not isinstance(result, dict):
        raise ToolError("pms_contract_error", "PMS create response was malformed.")

    safe_result = staff_safe_value(result)
    if isinstance(safe_result, dict):
        reservation_id = safe_result.get("reservationId") or safe_result.get("id")
        if isinstance(reservation_id, str) and "pmsUrl" not in safe_result and "url" not in safe_result:
            safe_result["pmsUrl"] = f"{pms_base_url(config)}/reservations/{urllib.parse.quote(reservation_id)}"
    mark_reservation_draft_created(draft, result)
    return {"ok": True, "reservation": safe_result}


def tool_hotel_registro_get_by_reservation(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    reservation_id = validate_safe_id("reservationId", args.get("reservationId"))
    result = pms_tool(config, "registro_get_by_reservation", {"reservationId": reservation_id}, profile="registro_read")
    registration_id = registration_id_from(result)
    return {
        "ok": True,
        "reservationId": reservation_id,
        "hasRegistration": bool(registration_id),
        "registrationId": registration_id,
        "registration": scrub_registro_metadata(result),
    }


def tool_hotel_registro_list_guests(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    registration_id = validate_safe_id("registrationId", args.get("registrationId"))
    result = pms_tool(config, "registro_list_guests", {"registrationId": registration_id}, profile="registro_read")
    guests = guests_from_registro_response(result, registration_id)
    return {"ok": True, "registrationId": registration_id, "guests": scrub_registro_metadata(guests)}


def tool_hotel_registro_list_documents(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    registration_id = validate_safe_id("registrationId", args.get("registrationId"))
    registration_guest_id = validate_safe_id("registrationGuestId", args.get("registrationGuestId"), required=False)
    payload = clean_payload({"registrationId": registration_id, "registrationGuestId": registration_guest_id})
    result = pms_tool(config, "registro_list_documents", payload, profile="registro_read")
    documents = documents_from_registro_response(result)
    if registration_guest_id:
        documents = [
            doc for doc in documents
            if not document_guest_id(doc) or document_guest_id(doc) == registration_guest_id
        ]
    return {"ok": True, "registrationId": registration_id, "registrationGuestId": registration_guest_id, "documents": scrub_registro_metadata(documents)}


def record_guest_needs_review(config: dict[str, Any], registration_id: str, registration_guest_id: str, reason: str) -> dict[str, Any] | None:
    try:
        return pms_tool(
            config,
            "registro_mark_guest_needs_review",
            {"registrationId": registration_id, "registrationGuestId": registration_guest_id, "reason": reason[:1000]},
            profile="registro_write",
        )
    except ToolError:
        return None


def extract_guest_document(
    config: dict[str, Any],
    registration_id: str,
    guest: dict[str, Any],
    document: dict[str, Any],
    record: bool,
) -> dict[str, Any]:
    registration_guest_id = guest_id_from(guest)
    document_id = document_id_from(document)
    if not registration_guest_id:
        return {"status": "needs_review", "reason": "missing_registration_guest_id", "document": scrub_registro_metadata(document)}
    if not document_id:
        reason = "missing_document_id"
        if record:
            record_guest_needs_review(config, registration_id, registration_guest_id, reason)
        return {"registrationGuestId": registration_guest_id, "status": "needs_review", "reason": reason}
    fetch_token = document_fetch_token(document)
    if not fetch_token:
        reason = "document_fetch_token_missing"
        if record:
            record_guest_needs_review(config, registration_id, registration_guest_id, reason)
        return {"registrationGuestId": registration_guest_id, "documentId": document_id, "status": "needs_review", "reason": reason}
    fetch = pms_tool(
        config,
        "registro_fetch_document",
        {"registrationId": registration_id, "registrationGuestId": registration_guest_id, "documentId": document_id, "fetchToken": fetch_token},
        profile="registro_read",
    )
    if not isinstance(fetch, dict):
        raise ToolError("pms_contract_error", "PMS document fetch response was malformed.")
    content_type = fetch_response_content_type(fetch, document)
    if content_type not in REGISTRO_DOCUMENT_CONTENT_TYPES:
        reason = f"unsupported_document_content_type:{content_type or 'unknown'}"
        if record:
            record_guest_needs_review(config, registration_id, registration_guest_id, reason)
        return {"registrationGuestId": registration_guest_id, "documentId": document_id, "status": "needs_review", "reason": reason}
    encoded_file = fetch_response_base64(fetch)
    if not encoded_file:
        reason = "document_bytes_missing"
        if record:
            record_guest_needs_review(config, registration_id, registration_guest_id, reason)
        return {"registrationGuestId": registration_guest_id, "documentId": document_id, "status": "needs_review", "reason": reason}
    extraction = local_extract_registro_document(content_type, encoded_file, guest)
    if extraction is None:
        api_key = registro_vision_api_key(config)
        if not api_key:
            reason = "registro_vision_provider_not_configured"
            if record:
                record_guest_needs_review(config, registration_id, registration_guest_id, reason)
            return {"registrationGuestId": registration_guest_id, "documentId": document_id, "status": "needs_review", "reason": reason}
        extraction = openai_extract_registro_document(
            api_key,
            registro_vision_model(config),
            content_type,
            encoded_file,
            {"registrationGuestId": registration_guest_id, "documentId": document_id, "fileName": document.get("fileName")},
        )
    record_result = None
    if record:
        payload = clean_payload({
            "registrationId": registration_id,
            "registrationGuestId": registration_guest_id,
            "documentId": document_id,
            "docType": extraction.get("docType"),
            "docNumber": extraction.get("docNumber"),
            "nationalityIso": extraction.get("nationalityIso"),
            "nationalityLabel": extraction.get("nationalityLabel"),
            "primerApellido": extraction.get("primerApellido"),
            "segundoApellido": extraction.get("segundoApellido"),
            "nombres": extraction.get("nombres"),
            "firstName": extraction.get("firstName"),
            "lastName": extraction.get("lastName"),
            "displayName": extraction.get("displayName"),
            "fechaNacimiento": extraction.get("fechaNacimiento"),
            "sexo": extraction.get("sexo"),
            "docExpiry": extraction.get("docExpiry"),
            "docIssueDate": extraction.get("docIssueDate"),
            "docIssuingCountry": extraction.get("docIssuingCountry"),
            "sireRequired": extraction.get("sireRequired"),
            "extractionMethod": extraction.get("extractionMethod"),
            "mrzChecksumsOk": extraction.get("mrzChecksumsOk"),
            "extractionConfidence": extraction.get("extractionConfidence"),
            "validationErrors": extraction.get("validationErrors"),
        })
        record_result = pms_tool(config, "registro_record_guest_extraction", payload, profile="registro_write")
    return {
        "registrationGuestId": registration_guest_id,
        "documentId": document_id,
        "fileName": document.get("fileName"),
        "contentType": content_type,
        "status": "extracted_with_review_flags" if extraction.get("validationErrors") else "extracted",
        "extraction": {key: value for key, value in extraction.items() if key != "rawVisibleText"},
        "recorded": scrub_registro_metadata(record_result) if record_result is not None else None,
    }


def tool_hotel_registro_extract_reservation(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    reservation_id = validate_safe_id("reservationId", args.get("reservationId"))
    record = validate_bool("record", args.get("record"))
    if record is None:
        record = True
    registration_lookup = pms_tool(config, "registro_get_by_reservation", {"reservationId": reservation_id}, profile="registro_read")
    registration_id = registration_id_from(registration_lookup)
    if not registration_id:
        return {
            "ok": True,
            "reservationId": reservation_id,
            "status": "no_registration",
            "message": "No Registro record exists for this reservation yet.",
        }
    guests_result = pms_tool(config, "registro_list_guests", {"registrationId": registration_id}, profile="registro_read")
    guests = guests_from_registro_response(guests_result, registration_id)
    documents_result = pms_tool(config, "registro_list_documents", {"registrationId": registration_id}, profile="registro_read")
    documents = documents_from_registro_response(documents_result)
    outputs: list[dict[str, Any]] = []
    for guest in guests:
        registration_guest_id = guest_id_from(guest)
        guest_documents = [
            document for document in documents
            if registration_guest_id and document_guest_id(document) == registration_guest_id
        ]
        if not guest_documents and len(guests) == 1:
            guest_documents = documents
        if not guest_documents:
            reason = "no_document_for_guest"
            if registration_guest_id and record:
                record_guest_needs_review(config, registration_id, registration_guest_id, reason)
            outputs.append({
                "registrationGuestId": registration_guest_id,
                "displayName": guest.get("displayName") or guest.get("guestName"),
                "status": "needs_review",
                "reason": reason,
            })
            continue
        outputs.append(extract_guest_document(config, registration_id, guest, guest_documents[0], record))
    return {
        "ok": True,
        "reservationId": reservation_id,
        "registrationId": registration_id,
        "record": record,
        "guestCount": len(guests),
        "documentCount": len(documents),
        "results": outputs,
        "registration": scrub_registro_metadata(registration_lookup),
    }


def tool_hotel_registro_prepare_submissions(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    reservation_id = validate_safe_id("reservationId", args.get("reservationId"))
    requested = validate_submission_types(args.get("submissionTypes"))
    registration_lookup = pms_tool(config, "registro_get_by_reservation", {"reservationId": reservation_id}, profile="registro_read")
    registration_id = registration_id_from(registration_lookup)
    guests: list[dict[str, Any]] = []
    if registration_id:
        guests_result = pms_tool(config, "registro_list_guests", {"registrationId": registration_id}, profile="registro_read")
        guests = guests_from_registro_response(guests_result, registration_id)
    plan = build_registro_submission_plan(registration_lookup, guests, requested_submission_types=requested or None)
    return {
        "ok": True,
        "reservationId": reservation_id,
        "registrationId": registration_id,
        "plan": plan,
    }


def safe_government_submission_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolError("pms_contract_error", "PMS government submission response was malformed.")
    safe_keys = (
        "registrationId",
        "reservationId",
        "submissionType",
        "status",
        "idempotencyKey",
        "receiptGranularity",
        "dueSubmissionTypes",
        "missingFields",
        "warnings",
    )
    summary = {key: staff_safe_value(value.get(key)) for key in safe_keys if value.get(key) not in (None, "", [], {})}
    payload = value.get("payload")
    if isinstance(payload, dict):
        guests = payload.get("guests")
        if isinstance(guests, list):
            summary["guestCount"] = len(guests)
        reservation = payload.get("reservation")
        if isinstance(reservation, dict):
            for key in ("arrivalDate", "departureDate"):
                if reservation.get(key):
                    summary[key] = staff_safe_value(reservation.get(key))
    return {key: child for key, child in summary.items() if child not in (None, "", [], {})}


def validate_submit_mode(value: Any) -> str:
    text = str(value or "dry_run").strip().lower().replace("-", "_")
    if text not in REGISTRO_SUBMIT_MODES:
        raise ToolError("invalid_input", "mode must be dry_run or submit.")
    return text


def government_submitter_enabled(config: dict[str, Any]) -> bool:
    return cfg_bool(config, "REGISTRO_GOVERNMENT_SUBMITTER_ENABLED", default=False)


def tra_submission_url(config: dict[str, Any]) -> str | None:
    raw = cfg_env(config, "TRA_SUBMISSION_URL") or cfg_env(config, "TRA_API_URL")
    if not raw:
        return None
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("config_invalid", "TRA submission URL must be an https URL.")
    return raw


def tra_api_base_url(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "TRA_API_BASE_URL") or DEFAULT_TRA_API_BASE_URL
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("config_invalid", "TRA API base URL must be an https URL.")
    return raw.rstrip("/")


def tra_api_one_url(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "TRA_API_ONE_URL")
    if raw:
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ToolError("config_invalid", "TRA API one URL must be an https URL.")
        return raw
    return f"{tra_api_base_url(config)}/one/"


def tra_api_two_url(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "TRA_API_TWO_URL")
    if raw:
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ToolError("config_invalid", "TRA API two URL must be an https URL.")
        return raw
    return f"{tra_api_base_url(config)}/two/"


def tra_login_url(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "TRA_LOGIN_URL") or DEFAULT_TRA_LOGIN_URL
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("config_invalid", "TRA login URL must be an https URL.")
    return raw


def tra_new_guest_url(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "TRA_NEW_GUEST_URL") or DEFAULT_TRA_NEW_GUEST_URL
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("config_invalid", "TRA new guest URL must be an https URL.")
    return raw


def tra_registered_guests_url(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "TRA_REGISTERED_GUESTS_URL") or DEFAULT_TRA_REGISTERED_GUESTS_URL
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("config_invalid", "TRA registered-guests URL must be an https URL.")
    return raw


def tra_api_token(config: dict[str, Any]) -> str | None:
    return cfg_file(config, "TRA_API_TOKEN") or cfg_env(config, "TRA_API_TOKEN")


def tra_establishment_name(config: dict[str, Any]) -> str:
    return cfg_env(config, "TRA_ESTABLISHMENT_NAME") or "Owl's Watch"


def tra_establishment_rnt(config: dict[str, Any]) -> str | None:
    return (
        cfg_file(config, "TRA_RNT_ESTABLISHMENT")
        or cfg_env(config, "TRA_RNT_ESTABLISHMENT")
        or tra_username(config)
    )


def tra_username(config: dict[str, Any]) -> str | None:
    return cfg_file(config, "TRA_USERNAME") or cfg_file(config, "TRA_RNT") or cfg_env(config, "TRA_USERNAME") or cfg_env(config, "TRA_RNT")


def tra_password(config: dict[str, Any]) -> str | None:
    return cfg_file(config, "TRA_PASSWORD") or cfg_env(config, "TRA_PASSWORD")


def sire_login_url(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "SIRE_LOGIN_URL") or DEFAULT_SIRE_LOGIN_URL
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("config_invalid", "SIRE login URL must be an https URL.")
    return raw


def sire_submission_url(config: dict[str, Any]) -> str | None:
    raw = cfg_env(config, "SIRE_SUBMISSION_URL") or cfg_env(config, "SIRE_API_URL")
    if not raw:
        return None
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("config_invalid", "SIRE submission URL must be an https URL.")
    return raw


def sire_api_token(config: dict[str, Any]) -> str | None:
    return cfg_file(config, "SIRE_API_TOKEN") or cfg_env(config, "SIRE_API_TOKEN")


def sire_document_type_value(config: dict[str, Any]) -> str | None:
    return cfg_file(config, "SIRE_DOCUMENT_TYPE_VALUE") or cfg_env(config, "SIRE_DOCUMENT_TYPE_VALUE")


def sire_document_number(config: dict[str, Any]) -> str | None:
    return cfg_file(config, "SIRE_DOCUMENT_NUMBER") or cfg_env(config, "SIRE_DOCUMENT_NUMBER")


def sire_password(config: dict[str, Any]) -> str | None:
    return cfg_file(config, "SIRE_PASSWORD") or cfg_env(config, "SIRE_PASSWORD")


def sire_company_value(config: dict[str, Any]) -> str | None:
    return cfg_file(config, "SIRE_COMPANY_VALUE") or cfg_env(config, "SIRE_COMPANY_VALUE")


def sire_company_label(config: dict[str, Any]) -> str | None:
    return cfg_file(config, "SIRE_COMPANY_LABEL") or cfg_env(config, "SIRE_COMPANY_LABEL")


def sire_submitter_mode(config: dict[str, Any]) -> str:
    return (cfg_env(config, "SIRE_SUBMITTER_MODE") or "").strip().lower()


def sire_auth_header(config: dict[str, Any], token: str) -> str:
    scheme = (cfg_env(config, "SIRE_AUTH_SCHEME") or "Bearer").strip()
    if not scheme or not re.match(r"^[A-Za-z][A-Za-z0-9_-]{0,30}$", scheme):
        raise ToolError("config_invalid", "SIRE auth scheme is malformed.")
    return f"{scheme} {token}"


def prepared_government_payload(value: dict[str, Any]) -> dict[str, Any]:
    payload = value.get("payload")
    if not isinstance(payload, dict) or not payload:
        raise ToolError("pms_contract_error", "PMS prepared submission did not include an internal payload.")
    return payload


def receipt_reference_from_response(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    candidates: list[Any] = [
        value.get("receiptReference"),
        value.get("receipt"),
        value.get("reference"),
        value.get("referenceCode"),
        value.get("radicado"),
        value.get("numeroRadicado"),
        value.get("confirmationNumber"),
        value.get("submissionId"),
        value.get("id"),
    ]
    data = value.get("data")
    if isinstance(data, dict):
        candidates.extend([
            data.get("receiptReference"),
            data.get("receipt"),
            data.get("reference"),
            data.get("referenceCode"),
            data.get("radicado"),
            data.get("numeroRadicado"),
            data.get("confirmationNumber"),
            data.get("submissionId"),
            data.get("id"),
        ])
    for candidate in candidates:
        if candidate is None:
            continue
        text = str(candidate).strip()
        if text:
            return validate_text("receiptReference", text, max_len=200)
    return None


def safe_submission_response_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = (
        "ok",
        "success",
        "status",
        "code",
        "message",
        "receiptReference",
        "receipt",
        "reference",
        "referenceCode",
        "radicado",
        "numeroRadicado",
        "confirmationNumber",
        "submissionId",
        "id",
    )
    summary = {key: staff_safe_value(value.get(key)) for key in allowed if value.get(key) not in (None, "", [], {})}
    data = value.get("data")
    if isinstance(data, dict):
        nested = {key: staff_safe_value(data.get(key)) for key in allowed if data.get(key) not in (None, "", [], {})}
        if nested:
            summary["data"] = nested
    return {key: child for key, child in summary.items() if child not in (None, "", [], {})}


class SelectOptionParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.current_select: str | None = None
        self.current_option: dict[str, str] | None = None
        self.options: dict[str, list[dict[str, str]]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag.lower() == "select":
            select_id = attr.get("id") or attr.get("name")
            self.current_select = select_id
            if select_id:
                self.options.setdefault(select_id, [])
        elif tag.lower() == "option" and self.current_select:
            self.current_option = {
                "value": attr.get("value", ""),
                "id": attr.get("id", ""),
                "text": "",
            }

    def handle_data(self, data: str) -> None:
        if self.current_option is not None:
            self.current_option["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "option" and self.current_select and self.current_option is not None:
            self.current_option["text"] = html.unescape(self.current_option["text"]).strip()
            self.options.setdefault(self.current_select, []).append(self.current_option)
            self.current_option = None
        elif tag.lower() == "select":
            self.current_select = None


def csrf_token_from_html(page_html: str) -> str | None:
    match = re.search(
        r'name=["\']csrfmiddlewaretoken["\'][^>]*value=["\']([^"\']+)["\']',
        page_html,
        flags=re.I,
    )
    if not match:
        match = re.search(
            r'value=["\']([^"\']+)["\'][^>]*name=["\']csrfmiddlewaretoken["\']',
            page_html,
            flags=re.I,
        )
    return html.unescape(match.group(1)) if match else None


def jsf_view_state_from_html(page_html: str) -> str | None:
    patterns = (
        r'name=["\']javax\.faces\.ViewState["\'][^>]*value=["\']([^"\']+)["\']',
        r'value=["\']([^"\']+)["\'][^>]*name=["\']javax\.faces\.ViewState["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, page_html, flags=re.I)
        if match:
            return html.unescape(match.group(1))
    return None


def html_input_value(page_html: str, input_name: str) -> str | None:
    escaped = re.escape(input_name)
    patterns = (
        rf'name=["\']{escaped}["\'][^>]*value=["\']([^"\']*)["\']',
        rf'value=["\']([^"\']*)["\'][^>]*name=["\']{escaped}["\']',
        rf'id=["\']{escaped}["\'][^>]*value=["\']([^"\']*)["\']',
        rf'value=["\']([^"\']*)["\'][^>]*id=["\']{escaped}["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, page_html, flags=re.I)
        if match:
            return html.unescape(match.group(1))
    return None


def jsf_form_action(page_html: str, form_id: str, base_url: str) -> str | None:
    escaped = re.escape(form_id)
    match = re.search(
        rf'<form\b[^>]*(?:id|name)=["\']{escaped}["\'][^>]*>',
        page_html,
        flags=re.I,
    )
    if not match:
        return None
    tag = match.group(0)
    action = re.search(r'action=["\']([^"\']+)["\']', tag, flags=re.I)
    if not action:
        return base_url
    return urllib.parse.urljoin(base_url, html.unescape(action.group(1)))


def jsf_form_id_containing(page_html: str, token: str) -> str | None:
    token_index = page_html.find(token)
    if token_index < 0:
        return None
    prefix = page_html[:token_index]
    matches = list(re.finditer(r'<form\b[^>]*(?:id|name)=["\']([^"\']+)["\'][^>]*>', prefix, flags=re.I))
    if not matches:
        return None
    return html.unescape(matches[-1].group(1))


def normalized_choice(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def nested_value(value: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = value
        found = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current not in (None, "", [], {}):
            return current
    return None


def tra_document_type(value: Any) -> str | None:
    text = normalized_choice(value)
    if not text:
        return None
    if "pasaporte" in text or "passport" in text:
        return "Pasaporte"
    if text in {"cc", "c c", "cedula", "cedula ciudadania", "cedula de ciudadania", "id card"}:
        return "C.C"
    if text in {"ce", "c e", "cedula extranjeria", "cedula de extranjeria"}:
        return "C.E"
    if "pep" in text:
        return "P.E.P"
    if "dni" in text or "documento nacional" in text:
        return "D.N.I"
    return None


def tra_country_option(options: dict[str, list[dict[str, str]]], country: Any) -> dict[str, str] | None:
    target = normalized_choice(country)
    if not target:
        return None
    aliases = {
        "usa": "estados unidos",
        "us": "estados unidos",
        "united states": "estados unidos",
        "united states of america": "estados unidos",
        "uk": "reino unido",
        "united kingdom": "reino unido",
        "south korea": "corea del sur",
    }
    target = aliases.get(target, target)
    for option in options.get("pais", []):
        if normalized_choice(option.get("text")) == target:
            return option
    for option in options.get("pais", []):
        choice = normalized_choice(option.get("text"))
        if choice and (choice.startswith(target) or target.startswith(choice)):
            return option
    return None


def tra_city_option(options: dict[str, list[dict[str, str]]], select_id: str, country_value: str, city: Any) -> str | None:
    target = normalized_choice(city)
    if not target:
        return None
    candidates = [option for option in options.get(select_id, []) if option.get("id") == country_value]
    for option in candidates:
        if normalized_choice(option.get("text")) == target:
            return option.get("value")
    for option in candidates:
        choice = normalized_choice(option.get("text"))
        city_prefix = normalized_choice(str(option.get("text") or "").split("-")[0])
        if choice.startswith(target) or target.startswith(choice) or city_prefix == target:
            return option.get("value")
    return None


def tra_payload_cost(payload: dict[str, Any]) -> Any:
    return first_present(
        nested_value(payload, "tra.totalCostCop", "tra.costo", "reservation.totalCostCop", "reservation.costo", "reservation.total"),
        payload.get("totalCostCop"),
        payload.get("costo"),
    )


def tra_payload_room_number(payload: dict[str, Any], config: dict[str, Any]) -> Any:
    return first_present(
        nested_value(payload, "tra.roomNumber", "tra.unitNumber", "reservation.roomNumber", "reservation.unitNumber", "reservation.unitCode"),
        payload.get("roomNumber"),
        cfg_env(config, "TRA_DEFAULT_ROOM_NUMBER"),
    )


def tra_payload_accommodation(payload: dict[str, Any], guest_count: int, config: dict[str, Any]) -> str:
    configured = first_present(
        nested_value(payload, "tra.accommodationType", "reservation.accommodationType"),
        cfg_env(config, "TRA_DEFAULT_ACCOMMODATION_TYPE"),
    )
    if configured:
        text = str(configured).strip()
        aliases = {
            "cabin": "Doble",
            "cabana": "Doble",
            "cabaña": "Doble",
            "guide-cabin": "Sencilla",
        }
        return aliases.get(normalized_choice(text), text)
    return "Sencilla" if guest_count <= 1 else "Doble"


def tra_payload_motivo(payload: dict[str, Any], config: dict[str, Any]) -> str:
    value = first_present(nested_value(payload, "tra.motivo", "reservation.travelReason", "travelReason"), cfg_env(config, "TRA_DEFAULT_TRAVEL_REASON"))
    if value:
        text = normalized_choice(value)
        if "negocio" in text:
            return "Negocios y motivos profesionales"
        if "famil" in text or "amigo" in text:
            return "Visitas a familiares y a amigos"
        if "salud" in text:
            return "Salud y atención médica"
        if "transito" in text:
            return "Tránsito"
        if "otro" in text:
            return "Otros motivos"
    return "Vacaciones, recreo y ocio"


def build_tra_form_fields(config: dict[str, Any], prepared: dict[str, Any], page_html: str) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    payload = prepared_government_payload(prepared)
    guests = payload.get("guests") if isinstance(payload.get("guests"), list) else []
    primary = guests[0] if guests and isinstance(guests[0], dict) else {}
    parser = SelectOptionParser()
    parser.feed(page_html)
    options = parser.options

    doc_type = tra_document_type(first_present(
        primary.get("documentType"),
        primary.get("docType"),
        primary.get("tipoIdentificacion"),
        primary.get("tipo_identificacion"),
    ))
    residence_country = first_present(
        primary.get("residenceCountry"),
        primary.get("residenceCountryName"),
        primary.get("countryOfResidence"),
        nested_value(payload, "tra.residenceCountry", "reservation.residenceCountry"),
    )
    residence_city = first_present(
        primary.get("residenceCity"),
        primary.get("residenceCityName"),
        primary.get("cityOfResidence"),
        nested_value(payload, "tra.residenceCity", "reservation.residenceCity"),
    )
    origin_country = first_present(
        primary.get("originCountry"),
        primary.get("originCountryName"),
        nested_value(payload, "tra.originCountry", "reservation.originCountry"),
    )
    origin_city = first_present(
        primary.get("originCity"),
        primary.get("originCityName"),
        nested_value(payload, "tra.originCity", "reservation.originCity"),
    )
    residence_country_option = tra_country_option(options, residence_country)
    origin_country_option = tra_country_option(options, origin_country)
    residence_city_value = tra_city_option(options, "ciudad", residence_country_option.get("value", "") if residence_country_option else "", residence_city)
    origin_city_value = tra_city_option(options, "ciudad2", origin_country_option.get("value", "") if origin_country_option else "", origin_city)

    cost = tra_payload_cost(payload)
    room_number = tra_payload_room_number(payload, config)
    guest_count = max(len(guests), 1)
    fields = {
        "csrfmiddlewaretoken": csrf_token_from_html(page_html),
        "tipo_identificacion": doc_type,
        "numero_identificacion": first_present(primary.get("documentNumber"), primary.get("docNumber"), primary.get("numeroIdentificacion")),
        "nombres": first_present(primary.get("firstName"), primary.get("firstNames"), primary.get("givenNames"), primary.get("nombres")),
        "apellidos": first_present(primary.get("lastName"), primary.get("lastNames"), primary.get("surname"), primary.get("surnames"), primary.get("apellidos")),
        "pais_residencia": residence_country_option.get("value") if residence_country_option else None,
        "ciudad_residencia": residence_city_value,
        "pais_procedencia": origin_country_option.get("value") if origin_country_option else None,
        "ciudad_procedencia": origin_city_value,
        "numero_habitacion": room_number,
        "motivo": tra_payload_motivo(payload, config),
        "numero_acompanantes": str(max(guest_count - 1, 0)),
        "check_in": first_present(nested_value(payload, "reservation.arrivalDate", "reservation.checkInDate", "tra.checkInDate"), payload.get("arrivalDate")),
        "check_out": first_present(nested_value(payload, "reservation.departureDate", "reservation.checkOutDate", "tra.checkOutDate"), payload.get("departureDate")),
        "tipo_acomodacion": tra_payload_accommodation(payload, guest_count, config),
        "costo": cost,
        "datos": "1",
        "acepto": "2",
    }
    missing = [key for key, value in fields.items() if value in (None, "")]
    if missing:
        return [], {
            "ok": False,
            "status": "blocked",
            "reason": "tra_payload_missing_required_fields",
            "missingFields": missing,
        }
    form_fields = [
        ("csrfmiddlewaretoken", str(fields["csrfmiddlewaretoken"])),
        ("tipo_identificacion", str(fields["tipo_identificacion"])),
        ("numero_identificacion", str(fields["numero_identificacion"])),
        ("nombres", str(fields["nombres"])),
        ("apellidos", str(fields["apellidos"])),
        ("dept", str(fields["pais_residencia"])),
        ("ciudad_residencia", str(fields["ciudad_residencia"])),
        ("dept", str(fields["pais_procedencia"])),
        ("ciudad_procedencia", str(fields["ciudad_procedencia"])),
        ("numero_habitacion", str(fields["numero_habitacion"])),
        ("motivo", str(fields["motivo"])),
        ("numero_acompanantes", str(fields["numero_acompanantes"])),
        ("check_in", str(fields["check_in"])),
        ("check_out", str(fields["check_out"])),
        ("tipo_acomodacion", str(fields["tipo_acomodacion"])),
        ("costo", str(fields["costo"])),
        ("datos", "1"),
        ("acepto", "2"),
    ]
    safe_summary = {
        "documentType": fields["tipo_identificacion"],
        "guestCount": guest_count,
        "checkIn": fields["check_in"],
        "checkOut": fields["check_out"],
        "roomNumber": fields["numero_habitacion"],
        "accommodationType": fields["tipo_acomodacion"],
        "residenceCountry": residence_country,
        "residenceCity": residence_city,
        "originCountry": origin_country,
        "originCity": origin_city,
    }
    return form_fields, {"ok": True, "status": "ready", "summary": safe_summary}


def tra_guest_city_value(guest: dict[str, Any], *keys: str) -> Any:
    return first_present(*(guest.get(key) for key in keys))


def build_tra_api_guest_payload(
    config: dict[str, Any],
    prepared: dict[str, Any],
    guest: dict[str, Any],
    *,
    guest_count: int,
    companion_parent_code: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = prepared_government_payload(prepared)
    reservation = payload.get("reservation") if isinstance(payload.get("reservation"), dict) else {}
    check_in = first_present(
        reservation.get("arrivalDate"),
        reservation.get("checkInDate"),
        nested_value(payload, "tra.checkInDate"),
        payload.get("arrivalDate"),
    )
    check_out = first_present(
        reservation.get("departureDate"),
        reservation.get("checkOutDate"),
        nested_value(payload, "tra.checkOutDate"),
        payload.get("departureDate"),
    )
    room_number = tra_payload_room_number(payload, config)
    doc_type = tra_document_type(first_present(
        guest.get("documentType"),
        guest.get("docType"),
        guest.get("tipoIdentificacion"),
        guest.get("tipo_identificacion"),
    ))
    base_fields = {
        "tipo_identificacion": doc_type,
        "numero_identificacion": first_present(guest.get("documentNumber"), guest.get("docNumber"), guest.get("numeroIdentificacion")),
        "nombres": first_present(guest.get("firstName"), guest.get("firstNames"), guest.get("givenNames"), guest.get("nombres")),
        "apellidos": first_present(guest.get("lastName"), guest.get("lastNames"), guest.get("surname"), guest.get("surnames"), guest.get("apellidos")),
        # The official TRA PMS manual intentionally spells these API keys as
        # "cuidad_*". The portal form uses "ciudad_*", but the API examples do not.
        "cuidad_residencia": tra_guest_city_value(guest, "residenceCity", "residenceCityName", "cityOfResidence"),
        "cuidad_procedencia": tra_guest_city_value(guest, "originCity", "originCityName"),
    }
    if companion_parent_code:
        fields = {
            "padre": companion_parent_code,
            **base_fields,
            "numero_habitacion": room_number,
            "check_in": check_in,
            "check_out": check_out,
        }
    else:
        fields = {
            **base_fields,
            "numero_habitacion": room_number,
            "motivo": tra_payload_motivo(payload, config),
            "numero_acompanantes": str(max(guest_count - 1, 0)),
            "check_in": check_in,
            "check_out": check_out,
            "tipo_acomodacion": tra_payload_accommodation(payload, guest_count, config),
            "costo": tra_payload_cost(payload),
            "nombre_establecimiento": tra_establishment_name(config),
            "rnt_establecimiento": tra_establishment_rnt(config),
        }
    missing = [key for key, value in fields.items() if value in (None, "")]
    if missing:
        return {}, {
            "ok": False,
            "status": "blocked",
            "reason": "tra_api_payload_missing_required_fields",
            "missingFields": missing,
        }
    cleaned = {key: str(value) for key, value in fields.items()}
    safe_summary = {
        "documentType": cleaned["tipo_identificacion"],
        "hasParent": bool(companion_parent_code),
    }
    if not companion_parent_code:
        safe_summary.update({
            "guestCount": guest_count,
            "checkIn": cleaned["check_in"],
            "checkOut": cleaned["check_out"],
            "roomNumber": cleaned["numero_habitacion"],
            "accommodationType": cleaned["tipo_acomodacion"],
        })
    return cleaned, {"ok": True, "status": "ready", "summary": safe_summary}


def build_tra_api_payloads(config: dict[str, Any], prepared: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    payload = prepared_government_payload(prepared)
    guests = payload.get("guests") if isinstance(payload.get("guests"), list) else []
    normalized_guests = [guest for guest in guests if isinstance(guest, dict)]
    if not normalized_guests:
        return {}, [], {
            "ok": False,
            "status": "blocked",
            "reason": "tra_api_payload_missing_guests",
            "missingFields": ["guests"],
        }
    primary_payload, primary_result = build_tra_api_guest_payload(
        config,
        prepared,
        normalized_guests[0],
        guest_count=len(normalized_guests),
    )
    if not primary_result.get("ok"):
        return {}, [], primary_result
    companions: list[dict[str, Any]] = []
    missing: list[str] = []
    for index, guest in enumerate(normalized_guests[1:], start=1):
        companion_payload, companion_result = build_tra_api_guest_payload(
            config,
            prepared,
            guest,
            guest_count=len(normalized_guests),
            companion_parent_code="__PARENT_CODE__",
        )
        if not companion_result.get("ok"):
            for field in companion_result.get("missingFields", []):
                missing.append(f"guests[{index}].{field}")
            continue
        companions.append(companion_payload)
    if missing:
        return {}, [], {
            "ok": False,
            "status": "blocked",
            "reason": "tra_api_payload_missing_required_fields",
            "missingFields": missing,
        }
    return primary_payload, companions, {
        "ok": True,
        "status": "ready",
        "summary": {
            **primary_result.get("summary", {}),
            "companionCount": len(companions),
        },
    }


def tra_api_primary_code(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    candidates: list[Any] = [
        response.get("code"),
        response.get("codigo"),
        response.get("id"),
        response.get("registro"),
        response.get("receiptReference"),
    ]
    data = response.get("data")
    if isinstance(data, dict):
        candidates.extend([
            data.get("code"),
            data.get("codigo"),
            data.get("id"),
            data.get("registro"),
            data.get("receiptReference"),
        ])
    for candidate in candidates:
        if candidate is None:
            continue
        text = str(candidate).strip()
        if text:
            return validate_text("traPrimaryCode", text, max_len=200)
    return None


def call_tra_api_submitter(config: dict[str, Any], prepared: dict[str, Any], token: str) -> dict[str, Any]:
    primary_payload, companions, payload_result = build_tra_api_payloads(config, prepared)
    if not payload_result.get("ok"):
        return payload_result
    headers = {
        "Authorization": f"token {token}",
        "x-ow-request-source": "hotel_registro_submitter",
        "x-ow-correlation-id": f"hotel-tra-{int(time.time())}-{os.getpid()}",
    }
    primary_response = http_json(tra_api_one_url(config), primary_payload, headers, timeout=45)
    primary_code = tra_api_primary_code(primary_response)
    if not primary_code:
        return {
            "ok": False,
            "status": "failed",
            "reason": "tra_response_missing_primary_code",
            "responseSummary": safe_submission_response_summary(primary_response),
        }
    companion_summaries: list[dict[str, Any]] = []
    for companion in companions:
        companion_payload = {**companion, "padre": primary_code}
        companion_response = http_json(tra_api_two_url(config), companion_payload, headers, timeout=45)
        companion_summaries.append(safe_submission_response_summary(companion_response))
    return {
        "ok": True,
        "status": "submitted",
        "receiptReference": primary_code,
        "responseSummary": {
            "status": "submitted_to_tra_api",
            "referenceType": "tra_api_code",
            "primary": safe_submission_response_summary(primary_response),
            "companions": companion_summaries,
        },
    }


def record_government_submission(
    config: dict[str, Any],
    *,
    registration_id: str,
    submission_type: str,
    state: str,
    receipt_reference: str | None = None,
    idempotency_key: str | None = None,
    payload_summary: dict[str, Any] | None = None,
    response_summary: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> Any:
    payload = clean_payload({
        "registrationId": registration_id,
        "submissionType": submission_type,
        "state": state,
        "attemptedAt": now_iso(),
        "receiptReference": receipt_reference,
        "idempotencyKey": idempotency_key,
        "errorCode": error_code,
        "errorMessage": error_message,
        "payloadSummary": clean_payload({
            "source": "hotel_registro_submit_government",
            **(payload_summary or {}),
        }),
        "responseSummary": clean_payload({
            "recordedBy": "hotel_agent",
            "governmentSubmitted": state == "submitted",
            **(response_summary or {}),
        }),
    })
    return pms_tool(config, "registro_record_submission", payload, profile="registro_write")


def call_tra_submitter(config: dict[str, Any], prepared: dict[str, Any]) -> dict[str, Any]:
    url = tra_submission_url(config)
    token = tra_api_token(config)
    if token:
        if url:
            response = http_json(
                url,
                prepared_government_payload(prepared),
                {
                    "Authorization": f"Bearer {token}",
                    "x-ow-request-source": "hotel_registro_submitter",
                    "x-ow-correlation-id": f"hotel-tra-{int(time.time())}-{os.getpid()}",
                },
                timeout=45,
            )
            receipt = receipt_reference_from_response(response)
            if not receipt:
                return {
                    "ok": False,
                    "status": "failed",
                    "reason": "tra_response_missing_receipt",
                    "responseSummary": safe_submission_response_summary(response),
                }
            return {
                "ok": True,
                "status": "submitted",
                "receiptReference": receipt,
                "responseSummary": safe_submission_response_summary(response),
            }
        return call_tra_api_submitter(config, prepared, token)
    return call_tra_form_submitter(config, prepared)


def http_text_with_opener(
    opener: urllib.request.OpenerDirector,
    url: str,
    payload: list[tuple[str, str]] | dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[str, str]:
    data = None
    if payload is not None:
        data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0 HotelRegistroAgent/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
            **(headers or {}),
        },
        method="POST" if data is not None else "GET",
    )
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.geturl(), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise ToolError("http_error", f"TRA request failed with HTTP {exc.code}.", retryable=500 <= exc.code < 600) from exc
    except urllib.error.URLError as exc:
        raise ToolError("network_error", "TRA network request failed.", retryable=True) from exc


def tra_login_session(config: dict[str, Any]) -> tuple[urllib.request.OpenerDirector | None, dict[str, Any]]:
    username = tra_username(config)
    password = tra_password(config)
    if not username or not password:
        return None, {"ok": False, "status": "blocked", "reason": "tra_credentials_missing"}
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login_url = tra_login_url(config)
    _, login_page = http_text_with_opener(opener, login_url)
    csrf = csrf_token_from_html(login_page)
    if not csrf:
        return None, {"ok": False, "status": "blocked", "reason": "tra_login_csrf_missing"}
    final_url, body = http_text_with_opener(
        opener,
        login_url,
        [
            ("csrfmiddlewaretoken", csrf),
            ("username", str(username)),
            ("password", str(password)),
        ],
        headers={"Referer": login_url},
    )
    if "/home" not in final_url and "Nuevo Huésped" not in body and "/padd" not in body:
        reason = "tra_login_failed_or_challenge_required"
        if "captcha" in body.lower() or "turnstile" in body.lower() or "cf-" in body.lower():
            reason = "tra_login_challenge_required"
        return None, {"ok": False, "status": "blocked", "reason": reason}
    return opener, {"ok": True, "status": "authenticated"}


def tra_guest_visible_in_registered_page(page_html: str, prepared: dict[str, Any]) -> bool:
    payload = prepared_government_payload(prepared)
    guests = payload.get("guests") if isinstance(payload.get("guests"), list) else []
    primary = guests[0] if guests and isinstance(guests[0], dict) else {}
    doc_number = str(first_present(primary.get("documentNumber"), primary.get("docNumber"), primary.get("numeroIdentificacion")) or "").strip()
    check_in = str(first_present(nested_value(payload, "reservation.arrivalDate", "reservation.checkInDate", "tra.checkInDate"), payload.get("arrivalDate")) or "").strip()
    if not doc_number:
        return False
    normalized_page = normalized_choice(page_html)
    if normalized_choice(doc_number) not in normalized_page:
        return False
    if check_in and normalized_choice(check_in) not in normalized_page:
        compact_date = check_in.replace("-", "")
        return compact_date in re.sub(r"[^0-9]", "", page_html)
    return True


def tra_receipt_reference(prepared: dict[str, Any]) -> str:
    payload = prepared_government_payload(prepared)
    guests = payload.get("guests") if isinstance(payload.get("guests"), list) else []
    primary = guests[0] if guests and isinstance(guests[0], dict) else {}
    doc_number = str(first_present(primary.get("documentNumber"), primary.get("docNumber"), primary.get("numeroIdentificacion")) or "guest").strip()
    check_in = str(first_present(nested_value(payload, "reservation.arrivalDate", "reservation.checkInDate", "tra.checkInDate"), payload.get("arrivalDate")) or now_iso()[:10]).strip()
    digest = hashlib.sha256(f"{doc_number}:{check_in}".encode("utf-8")).hexdigest()[:10].upper()
    return f"TRA-VISIBLE-{digest}"


def call_tra_form_submitter(config: dict[str, Any], prepared: dict[str, Any]) -> dict[str, Any]:
    opener, login = tra_login_session(config)
    if not opener:
        return login
    new_guest_url = tra_new_guest_url(config)
    registered_url = tra_registered_guests_url(config)
    _, form_page = http_text_with_opener(opener, new_guest_url)
    form_fields, form_result = build_tra_form_fields(config, prepared, form_page)
    if not form_result.get("ok"):
        return form_result

    _, registered_before = http_text_with_opener(opener, registered_url)
    if tra_guest_visible_in_registered_page(registered_before, prepared):
        return {
            "ok": True,
            "status": "submitted",
            "receiptReference": tra_receipt_reference(prepared),
            "responseSummary": {
                "status": "already_visible_in_tra",
                "referenceType": "tra_registered_guest_table",
            },
        }

    final_url, response_page = http_text_with_opener(
        opener,
        new_guest_url,
        form_fields,
        headers={"Referer": new_guest_url},
    )
    _, registered_after = http_text_with_opener(opener, registered_url)
    if tra_guest_visible_in_registered_page(registered_after, prepared) or tra_guest_visible_in_registered_page(response_page, prepared):
        return {
            "ok": True,
            "status": "submitted",
            "receiptReference": tra_receipt_reference(prepared),
            "responseSummary": {
                "status": "visible_in_tra_after_submit",
                "referenceType": "tra_registered_guest_table",
                "finalUrl": final_url,
            },
        }
    if "captcha" in response_page.lower() or "turnstile" in response_page.lower():
        return {"ok": False, "status": "blocked", "reason": "tra_submit_challenge_required"}
    return {
        "ok": False,
        "status": "failed",
        "reason": "tra_form_submission_unverified",
        "responseSummary": {"finalUrl": final_url},
    }


def sire_http_text(
    opener: urllib.request.OpenerDirector,
    url: str,
    payload: list[tuple[str, str]] | dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[str, str]:
    data = None
    if payload is not None:
        data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "Mozilla/5.0 HotelRegistroAgent/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
            **(headers or {}),
        },
        method="POST" if data is not None else "GET",
    )
    try:
        with opener.open(req, timeout=timeout) as response:
            return response.geturl(), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise ToolError("http_error", f"SIRE request failed with HTTP {exc.code}.", retryable=500 <= exc.code < 600) from exc
    except urllib.error.URLError as exc:
        raise ToolError("network_error", "SIRE network request failed.", retryable=True) from exc


def sire_login_session(config: dict[str, Any]) -> tuple[urllib.request.OpenerDirector | None, str | None, dict[str, Any]]:
    document_type = sire_document_type_value(config)
    document_number = sire_document_number(config)
    password = sire_password(config)
    company_value = sire_company_value(config)
    if not document_type or not document_number or not password or not company_value:
        return None, None, {"ok": False, "status": "blocked", "reason": "sire_credentials_missing"}

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login_url = sire_login_url(config)
    _, login_page = sire_http_text(opener, login_url)
    view_state = jsf_view_state_from_html(login_page)
    if not view_state:
        return None, None, {"ok": False, "status": "blocked", "reason": "sire_login_view_state_missing"}

    _, company_page = sire_http_text(
        opener,
        login_url,
        [
            ("AJAXREQUEST", "_viewRoot"),
            ("formLogin", "formLogin"),
            ("formLogin:tipoDocumento", str(document_type)),
            ("formLogin:numeroDocumento", str(document_number)),
            ("formLogin:password", ""),
            ("formLogin:listaEmpresa", "-1"),
            ("formLogin:infoSolicitudOpenedState", ""),
            ("javax.faces.ViewState", view_state),
            ("formLogin:j_id19", "formLogin:j_id19"),
            ("ajaxSingle", "formLogin:numeroDocumento"),
            ("AJAX:EVENTS_COUNT", "1"),
        ],
        headers={"Referer": login_url},
    )
    view_state = jsf_view_state_from_html(company_page) or view_state
    if str(company_value) not in company_page:
        return None, None, {"ok": False, "status": "blocked", "reason": "sire_company_not_available"}

    final_url, home_page = sire_http_text(
        opener,
        login_url,
        [
            ("formLogin", "formLogin"),
            ("formLogin:tipoDocumento", str(document_type)),
            ("formLogin:numeroDocumento", str(document_number)),
            ("formLogin:password", str(password)),
            ("formLogin:listaEmpresa", str(company_value)),
            ("formLogin:button1", "Ingresar"),
            ("formLogin:infoSolicitudOpenedState", ""),
            ("javax.faces.ViewState", view_state),
        ],
        headers={"Referer": login_url},
    )
    normalized_home = normalized_choice(sire_text_from_html(home_page))
    if "cargar informacion" not in normalized_home or "owl s watch" not in normalized_home:
        reason = "sire_login_failed_or_challenge_required"
        if "captcha" in normalized_home:
            reason = "sire_login_challenge_required"
        if "cambiar contrasena" in normalized_home or "cambio de contrasena" in normalized_home:
            reason = "sire_password_change_required"
        return None, final_url, {"ok": False, "status": "blocked", "reason": reason}
    return opener, final_url, {"ok": True, "status": "authenticated", "homePage": home_page}


def sire_open_lodging_form(config: dict[str, Any]) -> tuple[urllib.request.OpenerDirector | None, str | None, str | None, dict[str, Any]]:
    opener, current_url, login = sire_login_session(config)
    if not opener:
        return None, current_url, None, login
    home_page = login.get("homePage")
    if not isinstance(home_page, str):
        return None, current_url, None, {"ok": False, "status": "blocked", "reason": "sire_home_missing"}
    view_state = jsf_view_state_from_html(home_page)
    if not view_state:
        return None, current_url, None, {"ok": False, "status": "blocked", "reason": "sire_home_view_state_missing"}

    menu_url = jsf_form_action(home_page, "panelMenu:_form", sire_login_url(config))
    if not menu_url:
        menu_url = urllib.parse.urljoin(sire_login_url(config), "/sire/pages/home.jsf")
    _, cargar_page = sire_http_text(
        opener,
        menu_url,
        [
            ("panelMenu:_form", "panelMenu:_form"),
            ("panelMenuStategroupEmpresas", "opened"),
            ("panelMenuActiongroupEmpresas", ""),
            ("panelMenuActionitemCargarInformacion", "itemCargarInformacion"),
            ("panelMenuActionitemConsultarCargaInformacion", ""),
            ("panelMenuActionitemConsultarExtranjeros", ""),
            ("panelMenuActionitemActualizarDatosEmpresa", ""),
            ("panelMenuActionitemDescargaFormatoEmpresa", ""),
            ("panelMenuActionitemVinculaCuenta", ""),
            ("panelMenuselectedItemName", "itemCargarInformacion"),
            ("javax.faces.ViewState", view_state),
        ],
        headers={"Referer": menu_url},
    )
    if "HOTEL_server_submit" not in cargar_page:
        return None, menu_url, None, {"ok": False, "status": "blocked", "reason": "sire_lodging_tab_missing"}

    view_state = jsf_view_state_from_html(cargar_page)
    if not view_state:
        return None, menu_url, None, {"ok": False, "status": "blocked", "reason": "sire_cargar_view_state_missing"}
    tab_form_id = jsf_form_id_containing(cargar_page, "HOTEL_server_submit") or "j_id44:_form"
    cargar_url = jsf_form_action(cargar_page, tab_form_id, menu_url) or urllib.parse.urljoin(menu_url, "/sire/pages/empresas/cargueInformacion.jsf")
    final_url, hotel_page = sire_http_text(
        opener,
        cargar_url,
        [
            (tab_form_id, tab_form_id),
            ("HOTEL_server_submit", "HOTEL_server_submit"),
            ("javax.faces.ViewState", view_state),
        ],
        headers={"Referer": cargar_url},
    )
    if "cargueFormHospedaje" not in hotel_page:
        return None, final_url, None, {"ok": False, "status": "blocked", "reason": "sire_lodging_form_missing"}
    return opener, final_url, hotel_page, {"ok": True, "status": "ready"}


def sire_movement_type(submission_type: str) -> str:
    normalized = normalize_submission_type(submission_type)
    if normalized == "sire_entrada":
        return "entrada"
    if normalized == "sire_salida":
        return "salida"
    raise ToolError("invalid_input", "SIRE submission type must be sire_entrada or sire_salida.")


def sire_movement_date(payload: dict[str, Any], submission_type: str) -> Any:
    reservation = payload.get("reservation") if isinstance(payload.get("reservation"), dict) else {}
    if sire_movement_type(submission_type) == "entrada":
        return first_present(reservation.get("arrivalDate"), reservation.get("checkInDate"), payload.get("arrivalDate"))
    return first_present(reservation.get("departureDate"), reservation.get("checkOutDate"), payload.get("departureDate"))


def sire_document_type(value: Any) -> str | None:
    text = normalized_choice(value)
    if not text:
        return None
    if "pasaporte" in text or "passport" in text:
        return "Pasaporte"
    if text in {"cc", "c c", "cedula", "cedula ciudadania", "cedula de ciudadania", "id card"}:
        return "Cedula de Ciudadania"
    if text in {"ce", "c e", "cedula extranjeria", "cedula de extranjeria"}:
        return "Cedula de Extranjeria"
    if "pep" in text:
        return "PEP"
    if "ppt" in text or "proteccion temporal" in text:
        return "Permiso por Proteccion Temporal"
    if "dni" in text or "documento nacional" in text or "documento extranjero" in text:
        return "Documento Extranjero"
    return None


def split_sire_surnames(value: Any) -> tuple[str | None, str]:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None, ""
    parts = text.split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def build_sire_lodging_payload(
    config: dict[str, Any],
    prepared: dict[str, Any],
    submission_type: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized_type = normalize_submission_type(submission_type)
    if normalized_type not in {"sire_entrada", "sire_salida"}:
        raise ToolError("invalid_input", "SIRE payload builder only accepts SIRE submission types.")
    payload = prepared_government_payload(prepared)
    guests = payload.get("guests") if isinstance(payload.get("guests"), list) else []
    movement_date = sire_movement_date(payload, normalized_type)
    movement = sire_movement_type(normalized_type)
    records: list[dict[str, Any]] = []
    missing: list[str] = []
    for index, guest in enumerate([item for item in guests if isinstance(item, dict)], start=1):
        if guest.get("sireRequired") is False:
            continue
        first_surname, second_surname = split_sire_surnames(first_present(
            guest.get("lastName"),
            guest.get("lastNames"),
            guest.get("surname"),
            guest.get("surnames"),
            guest.get("primerApellido"),
        ))
        record = {
            "tipoMovimiento": movement,
            "fechaMovimiento": movement_date,
            "tipoDocumento": sire_document_type(first_present(
                guest.get("documentType"),
                guest.get("docType"),
                guest.get("tipoIdentificacion"),
                guest.get("tipo_identificacion"),
            )),
            "numeroDocumento": first_present(guest.get("documentNumber"), guest.get("docNumber"), guest.get("numeroIdentificacion")),
            "fechaNacimiento": first_present(guest.get("birthDate"), guest.get("dateOfBirth"), guest.get("fechaNacimiento")),
            "primerApellido": first_surname,
            "segundoApellido": first_present(guest.get("secondLastName"), guest.get("segundoApellido"), second_surname),
            "nombres": first_present(guest.get("firstName"), guest.get("firstNames"), guest.get("givenNames"), guest.get("nombres")),
            "nacionalidad": first_present(guest.get("nationalityCountry"), guest.get("nationalityLabel"), guest.get("nationalityIso")),
            "paisProcedencia": first_present(guest.get("originCountry"), guest.get("originCountryName")),
            "paisProximoDestino": first_present(guest.get("destinationCountry"), guest.get("destinationCountryName")),
        }
        required = (
            "tipoMovimiento",
            "fechaMovimiento",
            "tipoDocumento",
            "numeroDocumento",
            "fechaNacimiento",
            "primerApellido",
            "nombres",
            "nacionalidad",
            "paisProcedencia",
            "paisProximoDestino",
        )
        for key in required:
            if record.get(key) in (None, ""):
                missing.append(f"guests[{index}].{key}")
        records.append({key: "" if value is None else str(value) for key, value in record.items()})
    if not records:
        return {}, {
            "ok": False,
            "status": "blocked",
            "reason": "sire_no_required_guests",
            "summary": {"submissionType": normalized_type, "recordCount": 0},
        }
    if missing:
        return {}, {
            "ok": False,
            "status": "blocked",
            "reason": "sire_payload_missing_required_fields",
            "missingFields": missing,
            "summary": {"submissionType": normalized_type, "recordCount": len(records), "movementType": movement, "movementDate": movement_date},
        }
    property_payload = payload.get("property") if isinstance(payload.get("property"), dict) else {}
    reservation = payload.get("reservation") if isinstance(payload.get("reservation"), dict) else {}
    submission_payload = {
        "submissionType": normalized_type,
        "reportType": "alojamiento_hospedaje",
        "movementType": movement,
        "movementDate": str(movement_date),
        "registrationId": prepared.get("registrationId"),
        "reservationId": prepared.get("reservationId") or reservation.get("reservationId"),
        "propertyId": property_payload.get("propertyId"),
        "records": records,
    }
    summary = {
        "submissionType": normalized_type,
        "reportType": "alojamiento_hospedaje",
        "movementType": movement,
        "movementDate": str(movement_date),
        "recordCount": len(records),
    }
    return submission_payload, {"ok": True, "status": "ready", "summary": summary}


def sire_form_movement_value(movement_type: str) -> str:
    normalized = normalized_choice(movement_type)
    if normalized == "entrada":
        return "3"
    if normalized == "salida":
        return "4"
    raise ToolError("invalid_input", "SIRE movement type must be entrada or salida.")


def sire_date_for_form(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        if DATE_RE.match(text):
            parsed = dt.date.fromisoformat(text)
        else:
            parsed = dt.datetime.strptime(text, "%d/%m/%Y").date()
        return parsed.strftime("%d/%m/%Y")
    except ValueError:
        return None


def sire_current_month_for_form(value: Any, fallback: str | None = None) -> str | None:
    if value in (None, ""):
        return fallback
    text = str(value).strip()
    try:
        parsed = dt.date.fromisoformat(text) if DATE_RE.match(text) else dt.datetime.strptime(text, "%d/%m/%Y").date()
        return parsed.strftime("%m/%Y")
    except ValueError:
        return fallback


def sire_document_type_value_from_options(options: dict[str, list[dict[str, str]]], value: Any) -> str | None:
    target = normalized_choice(value)
    aliases = {
        "pasaporte": "pasaporte",
        "passport": "pasaporte",
        "cedula extranjeria": "cedula de extranjeria",
        "cedula de extranjeria": "cedula de extranjeria",
        "ce": "cedula de extranjeria",
        "c e": "cedula de extranjeria",
        "documento extranjero": "documento extranjero",
        "dni": "documento extranjero",
        "pep": "permiso especial de permanencia",
        "permiso especial de permanencia": "permiso especial de permanencia",
        "ppt": "permiso por proteccion temporal",
        "permiso por proteccion temporal": "permiso por proteccion temporal",
        "carne diplomatico": "carne diplomatico",
    }
    target = aliases.get(target, target)
    for option in options.get("cargueFormHospedaje:tipoDocumento", []):
        text = normalized_choice(option.get("text"))
        if text == target:
            return option.get("value")
    for option in options.get("cargueFormHospedaje:tipoDocumento", []):
        text = normalized_choice(option.get("text"))
        if text and (text.startswith(target) or target.startswith(text)):
            return option.get("value")
    return None


def sire_country_value_from_options(options: dict[str, list[dict[str, str]]], select_id: str, country: Any) -> str | None:
    target = normalized_choice(country)
    aliases = {
        "usa": "estados unidos",
        "us": "estados unidos",
        "u s a": "estados unidos",
        "united states": "estados unidos",
        "united states of america": "estados unidos",
        "estados unidos de america": "estados unidos",
        "uk": "reino unido",
        "united kingdom": "reino unido",
        "great britain": "reino unido",
        "south korea": "corea del sur",
        "north korea": "corea del norte",
        "czech republic": "republica checa",
        "czechia": "republica checa",
        "chinese": "china",
        "chn": "china",
    }
    target = aliases.get(target, target)
    if not target:
        return None
    choices = options.get(select_id, [])
    for option in choices:
        if normalized_choice(option.get("text")) == target:
            return option.get("value")
    for option in choices:
        text = normalized_choice(option.get("text"))
        if text and (text.startswith(target) or target.startswith(text) or target in text):
            return option.get("value")
    return None


def build_sire_lodging_form_fields(
    config: dict[str, Any],
    payload: dict[str, Any],
    record: dict[str, Any],
    page_html: str,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    parser = SelectOptionParser()
    parser.feed(page_html)
    options = parser.options
    view_state = jsf_view_state_from_html(page_html)
    movement_date = sire_date_for_form(record.get("fechaMovimiento"))
    birth_date = sire_date_for_form(record.get("fechaNacimiento"))
    move_current = sire_current_month_for_form(record.get("fechaMovimiento"), html_input_value(page_html, "cargueFormHospedaje:fechaMovimientoInputCurrentDate"))
    birth_current = sire_current_month_for_form(record.get("fechaNacimiento"), html_input_value(page_html, "cargueFormHospedaje:fechaNacimientoInputCurrentDate"))
    fields = {
        "viewState": view_state,
        "movementValue": sire_form_movement_value(record.get("tipoMovimiento")),
        "movementDate": movement_date,
        "movementCurrent": move_current,
        "documentTypeValue": sire_document_type_value_from_options(options, record.get("tipoDocumento")),
        "documentNumber": record.get("numeroDocumento"),
        "birthDate": birth_date,
        "birthCurrent": birth_current,
        "firstSurname": record.get("primerApellido"),
        "secondSurname": record.get("segundoApellido", ""),
        "names": record.get("nombres"),
        "nationalityValue": sire_country_value_from_options(options, "cargueFormHospedaje:nacionalidad", record.get("nacionalidad")),
        "originValue": sire_country_value_from_options(options, "cargueFormHospedaje:procedencia", record.get("paisProcedencia")),
        "destinationValue": sire_country_value_from_options(options, "cargueFormHospedaje:destino", record.get("paisProximoDestino")),
    }
    required = (
        "viewState",
        "movementValue",
        "movementDate",
        "movementCurrent",
        "documentTypeValue",
        "documentNumber",
        "birthDate",
        "birthCurrent",
        "firstSurname",
        "names",
        "nationalityValue",
        "originValue",
        "destinationValue",
    )
    missing = [key for key in required if fields.get(key) in (None, "")]
    if missing:
        return [], {
            "ok": False,
            "status": "blocked",
            "reason": "sire_form_mapping_missing_required_fields",
            "missingFields": missing,
            "payloadSummary": payload.get("summary") if isinstance(payload.get("summary"), dict) else None,
        }
    form_fields = [
        ("AJAXREQUEST", "_viewRoot"),
        ("cargueFormHospedaje", "cargueFormHospedaje"),
        ("cargueFormHospedaje:tipoCargue", "F"),
        ("cargueFormHospedaje:tipoMovimiento", str(fields["movementValue"])),
        ("cargueFormHospedaje:fechaMovimientoInputDate", str(fields["movementDate"])),
        ("cargueFormHospedaje:fechaMovimientoInputCurrentDate", str(fields["movementCurrent"])),
        ("cargueFormHospedaje:tipoDocumento", str(fields["documentTypeValue"])),
        ("cargueFormHospedaje:numeroDocumento", str(fields["documentNumber"])),
        ("cargueFormHospedaje:fechaNacimientoInputDate", str(fields["birthDate"])),
        ("cargueFormHospedaje:fechaNacimientoInputCurrentDate", str(fields["birthCurrent"])),
        ("cargueFormHospedaje:primerApellido", str(fields["firstSurname"])),
        ("cargueFormHospedaje:segundoApellido", str(fields["secondSurname"] or "")),
        ("cargueFormHospedaje:nombres", str(fields["names"])),
        ("cargueFormHospedaje:nacionalidad", str(fields["nationalityValue"])),
        ("cargueFormHospedaje:procedencia", str(fields["originValue"])),
        ("cargueFormHospedaje:destino", str(fields["destinationValue"])),
        ("cargueFormHospedaje:j_id877", "cargueFormHospedaje:j_id877"),
        ("javax.faces.ViewState", str(fields["viewState"])),
        ("AJAX:EVENTS_COUNT", "1"),
    ]
    return form_fields, {
        "ok": True,
        "status": "ready",
        "summary": {
            "documentType": record.get("tipoDocumento"),
            "movementType": record.get("tipoMovimiento"),
            "movementDate": record.get("fechaMovimiento"),
            "nationality": record.get("nacionalidad"),
            "origin": record.get("paisProcedencia"),
            "destination": record.get("paisProximoDestino"),
        },
    }


def sire_find_save_control(page_html: str) -> str | None:
    candidates: list[tuple[int, str]] = []
    for match in re.finditer(r'<input\b[^>]*(?:value=["\']([^"\']+)["\'])[^>]*>', page_html, flags=re.I):
        tag = match.group(0)
        value = html.unescape(match.group(1))
        normalized = normalized_choice(value)
        if not any(word in normalized for word in ("guardar", "enviar", "finalizar")):
            continue
        name_match = re.search(r'name=["\']([^"\']+)["\']', tag, flags=re.I)
        id_match = re.search(r'id=["\']([^"\']+)["\']', tag, flags=re.I)
        control = html.unescape((name_match or id_match).group(1)) if (name_match or id_match) else None
        if control and control.startswith("cargueFormHospedaje:"):
            candidates.append((match.start(), control))
    if candidates:
        return candidates[-1][1]
    return None


def sire_text_from_html(page_html: str) -> str:
    text = re.sub(r"<script\b[\s\S]*?</script>", " ", page_html, flags=re.I)
    text = re.sub(r"<style\b[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def sire_extract_submission_counts(page_html: str) -> dict[str, int | None]:
    normalized = normalized_choice(sire_text_from_html(page_html))
    def find_count(*labels: str) -> int | None:
        for label in labels:
            target = normalized_choice(label)
            match = re.search(rf"{re.escape(target)}\s*:?\s*(\d+)", normalized)
            if match:
                return int(match.group(1))
        return None
    return {
        "processed": find_count("Total Registros procesados", "Registros procesados"),
        "valid": find_count("Num. Registros válidos", "Registros validos", "Registros válidos"),
        "invalid": find_count("Num. Registros inválidos", "Registros invalidos", "Registros inválidos"),
    }


def sire_verified_success(page_html: str, expected_records: int) -> tuple[bool, dict[str, int | None]]:
    counts = sire_extract_submission_counts(page_html)
    processed = counts.get("processed")
    valid = counts.get("valid")
    invalid = counts.get("invalid")
    if valid is not None and valid >= expected_records and (invalid in (None, 0)):
        return True, counts
    normalized = normalized_choice(sire_text_from_html(page_html))
    if "registros validos" in normalized and "registros invalidos 0" in normalized:
        return True, counts
    if "carga de la informacion" in normalized and "exitosa" in normalized and invalid in (None, 0):
        return True, counts
    if processed is not None and valid is None and processed >= expected_records and invalid in (None, 0):
        return True, counts
    return False, counts


def sire_receipt_reference(payload: dict[str, Any], submission_type: str) -> str:
    digest_source = json.dumps(
        {
            "submissionType": normalize_submission_type(submission_type),
            "registrationId": payload.get("registrationId"),
            "reservationId": payload.get("reservationId"),
            "movementDate": payload.get("movementDate"),
            "recordCount": len(payload.get("records") or []),
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:10].upper()
    movement_date = str(payload.get("movementDate") or now_iso()[:10]).replace("-", "")
    return f"SIRE-ALOJAMIENTO-{movement_date}-{digest}"


def call_sire_jsf_form_submitter(config: dict[str, Any], prepared: dict[str, Any], submission_type: str) -> dict[str, Any]:
    payload, payload_result = build_sire_lodging_payload(config, prepared, submission_type)
    if not payload_result.get("ok"):
        return payload_result
    opener, form_url, form_page, form_ready = sire_open_lodging_form(config)
    if not opener or not form_url or not form_page:
        return {
            **form_ready,
            "submissionType": normalize_submission_type(submission_type),
            "payloadSummary": payload_result.get("summary"),
        }

    current_page = form_page
    for record in payload.get("records") or []:
        fields, field_result = build_sire_lodging_form_fields(config, payload, record, current_page)
        if not field_result.get("ok"):
            return {
                **field_result,
                "submissionType": normalize_submission_type(submission_type),
                "payloadSummary": payload_result.get("summary"),
            }
        _, response_page = sire_http_text(opener, form_url, fields, headers={"Referer": form_url}, timeout=45)
        normalized_response = normalized_choice(sire_text_from_html(response_page))
        if "error" in normalized_response and ("validacion" in normalized_response or "obligatorio" in normalized_response):
            return {
                "ok": False,
                "status": "failed",
                "reason": "sire_record_validation_failed",
                "payloadSummary": payload_result.get("summary"),
                "responseSummary": {"status": "validation_error"},
            }
        if jsf_view_state_from_html(response_page):
            current_page = response_page

    save_control = sire_find_save_control(current_page)
    if not save_control:
        return {
            "ok": False,
            "status": "failed",
            "reason": "sire_save_control_missing",
            "payloadSummary": payload_result.get("summary"),
            "responseSummary": {"status": "records_not_saved"},
        }
    view_state = jsf_view_state_from_html(current_page)
    if not view_state:
        return {
            "ok": False,
            "status": "failed",
            "reason": "sire_save_view_state_missing",
            "payloadSummary": payload_result.get("summary"),
            "responseSummary": {"status": "records_not_saved"},
        }
    save_fields = [
        ("AJAXREQUEST", "_viewRoot"),
        ("cargueFormHospedaje", "cargueFormHospedaje"),
        ("cargueFormHospedaje:tipoCargue", "F"),
        (save_control, save_control),
        ("javax.faces.ViewState", view_state),
        ("AJAX:EVENTS_COUNT", "1"),
    ]
    final_url, save_page = sire_http_text(opener, form_url, save_fields, headers={"Referer": form_url}, timeout=60)
    expected = len(payload.get("records") or [])
    verified, counts = sire_verified_success(save_page, expected)
    if not verified:
        if "captcha" in normalized_choice(save_page):
            return {
                "ok": False,
                "status": "blocked",
                "reason": "sire_submit_challenge_required",
                "payloadSummary": payload_result.get("summary"),
            }
        return {
            "ok": False,
            "status": "failed",
            "reason": "sire_form_submission_unverified",
            "payloadSummary": payload_result.get("summary"),
            "responseSummary": {"finalUrl": final_url, "counts": counts},
        }
    return {
        "ok": True,
        "status": "submitted",
        "receiptReference": sire_receipt_reference(payload, submission_type),
        "payloadSummary": payload_result.get("summary"),
        "responseSummary": {
            "status": "submitted_to_sire_form",
            "referenceType": "sire_valid_record_counts",
            "counts": counts,
        },
    }


def call_sire_submitter(config: dict[str, Any], prepared: dict[str, Any], submission_type: str) -> dict[str, Any]:
    payload, payload_result = build_sire_lodging_payload(config, prepared, submission_type)
    if not payload_result.get("ok"):
        return payload_result
    mode = sire_submitter_mode(config)
    url = sire_submission_url(config)
    if not url:
        if mode == "jsf_form" or (
            sire_document_type_value(config)
            and sire_document_number(config)
            and sire_password(config)
            and sire_company_value(config)
        ):
            return call_sire_jsf_form_submitter(config, prepared, submission_type)
        return {
            "ok": False,
            "status": "blocked",
            "reason": "sire_submitter_not_configured",
            "submissionType": normalize_submission_type(submission_type),
            "loginUrl": sire_login_url(config),
            "payloadSummary": payload_result.get("summary"),
            "note": "SIRE payload validation is ready, but no verified SIRE API/browser adapter is configured.",
        }
    token = sire_api_token(config)
    if not token:
        return {
            "ok": False,
            "status": "blocked",
            "reason": "sire_api_token_missing",
            "submissionType": normalize_submission_type(submission_type),
            "payloadSummary": payload_result.get("summary"),
        }
    response = http_json(
        url,
        payload,
        {
            "Authorization": sire_auth_header(config, token),
            "x-ow-request-source": "hotel_registro_sire_submitter",
            "x-ow-correlation-id": f"hotel-sire-{int(time.time())}-{os.getpid()}",
        },
        timeout=60,
    )
    receipt = receipt_reference_from_response(response)
    if not receipt:
        return {
            "ok": False,
            "status": "failed",
            "reason": "sire_response_missing_receipt",
            "payloadSummary": payload_result.get("summary"),
            "responseSummary": safe_submission_response_summary(response),
        }
    return {
        "ok": True,
        "status": "submitted",
        "receiptReference": receipt,
        "payloadSummary": payload_result.get("summary"),
        "responseSummary": safe_submission_response_summary(response),
    }


def blocked_sire_submitter(config: dict[str, Any], submission_type: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "blocked",
        "reason": "sire_submitter_not_configured",
        "submissionType": submission_type,
        "loginUrl": sire_login_url(config),
        "note": "SIRE requires configured credentials and verified browser/API automation before live submission.",
    }


def tool_hotel_registro_prepare_government_submission(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    reservation_id = validate_safe_id("reservationId", args.get("reservationId"), required=False)
    registration_id = validate_safe_id("registrationId", args.get("registrationId"), required=False)
    requested = validate_submission_types(args.get("submissionTypes"))
    if not reservation_id and not registration_id:
        raise ToolError("invalid_input", "reservationId or registrationId is required.")

    registration_lookup: dict[str, Any] = {}
    if reservation_id:
        registration_lookup = pms_tool(config, "registro_get_by_reservation", {"reservationId": reservation_id}, profile="registro_read")
        registration_id = registration_id_from(registration_lookup)
    elif registration_id:
        registration_lookup = pms_tool(config, "registro_get", {"registrationId": registration_id}, profile="registro_read")
        record = registration_record_from(registration_lookup)
        if isinstance(record.get("reservationId"), str):
            reservation_id = record["reservationId"]

    if not registration_id:
        return {
            "ok": True,
            "reservationId": reservation_id,
            "registrationId": None,
            "status": "blocked",
            "blockers": [{"scope": "registration", "reason": "no_registration"}],
            "submissions": [],
        }

    guests_result = pms_tool(config, "registro_list_guests", {"registrationId": registration_id}, profile="registro_read")
    guests = guests_from_registro_response(guests_result, registration_id)
    plan = build_registro_submission_plan(registration_lookup, guests, requested_submission_types=requested or None)
    if plan.get("status") != "ready":
        return {
            "ok": True,
            "reservationId": reservation_id,
            "registrationId": registration_id,
            "status": plan.get("status"),
            "plan": plan,
            "submissions": [],
        }

    submissions: list[dict[str, Any]] = []
    for submission_type in plan.get("requestedSubmissionTypes") or []:
        prepared = pms_tool(
            config,
            "registro_prepare_government_submission",
            {"registrationId": registration_id, "submissionType": submission_type},
            profile="registro_read",
        )
        submissions.append(safe_government_submission_summary(prepared))

    return {
        "ok": True,
        "reservationId": reservation_id,
        "registrationId": registration_id,
        "status": "ready",
        "guestCount": plan.get("guestCount"),
        "readyGuestCount": plan.get("readyGuestCount"),
        "dueSubmissionTypes": plan.get("dueSubmissionTypes"),
        "submissions": submissions,
        "warnings": [
            "PMS government payloads are prepared but not exposed to the model.",
            "Live submission is gated by hotel_registro_submit_government and the runtime enable flag. TRA is configured; SIRE requires a verified adapter endpoint or browser routine.",
        ],
    }


def tool_hotel_registro_submit_government(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    reservation_id = validate_safe_id("reservationId", args.get("reservationId"), required=False)
    registration_id = validate_safe_id("registrationId", args.get("registrationId"), required=False)
    requested = validate_submission_types(args.get("submissionTypes"))
    mode = validate_submit_mode(args.get("mode"))
    if not reservation_id and not registration_id:
        raise ToolError("invalid_input", "reservationId or registrationId is required.")

    registration_lookup: dict[str, Any] = {}
    if reservation_id:
        registration_lookup = pms_tool(config, "registro_get_by_reservation", {"reservationId": reservation_id}, profile="registro_read")
        registration_id = registration_id_from(registration_lookup)
    elif registration_id:
        registration_lookup = pms_tool(config, "registro_get", {"registrationId": registration_id}, profile="registro_read")
        record = registration_record_from(registration_lookup)
        if isinstance(record.get("reservationId"), str):
            reservation_id = record["reservationId"]

    if not registration_id:
        return {
            "ok": True,
            "reservationId": reservation_id,
            "registrationId": None,
            "mode": mode,
            "status": "blocked",
            "blockers": [{"scope": "registration", "reason": "no_registration"}],
            "results": [],
        }

    guests_result = pms_tool(config, "registro_list_guests", {"registrationId": registration_id}, profile="registro_read")
    guests = guests_from_registro_response(guests_result, registration_id)
    plan = build_registro_submission_plan(registration_lookup, guests, requested_submission_types=requested or None)
    if plan.get("status") != "ready":
        return {
            "ok": True,
            "reservationId": reservation_id,
            "registrationId": registration_id,
            "mode": mode,
            "status": plan.get("status"),
            "plan": plan,
            "results": [],
        }

    prepared_items: list[dict[str, Any]] = []
    for submission_type in plan.get("requestedSubmissionTypes") or []:
        prepared = pms_tool(
            config,
            "registro_prepare_government_submission",
            {"registrationId": registration_id, "submissionType": submission_type},
            profile="registro_read",
        )
        prepared_items.append(prepared)

    if mode == "dry_run":
        return {
            "ok": True,
            "reservationId": reservation_id,
            "registrationId": registration_id,
            "mode": mode,
            "status": "ready",
            "guestCount": plan.get("guestCount"),
            "readyGuestCount": plan.get("readyGuestCount"),
            "dueSubmissionTypes": plan.get("dueSubmissionTypes"),
            "results": [safe_government_submission_summary(prepared) for prepared in prepared_items],
            "warnings": [
                "Dry run only. No government submission was attempted.",
                "Government payloads are held inside the tool and not exposed to the model.",
            ],
        }

    if not government_submitter_enabled(config):
        return {
            "ok": True,
            "reservationId": reservation_id,
            "registrationId": registration_id,
            "mode": mode,
            "status": "blocked",
            "results": [
                {
                    **safe_government_submission_summary(prepared),
                    "submitStatus": "blocked",
                    "reason": "government_submitter_disabled",
                }
                for prepared in prepared_items
            ],
            "warnings": [
                "Live government submission is disabled in the Hotel tool environment.",
                "Set REGISTRO_GOVERNMENT_SUBMITTER_ENABLED=1 only after TRA/SIRE credentials and adapters are verified.",
            ],
        }

    results: list[dict[str, Any]] = []
    overall_status = "submitted"
    for prepared in prepared_items:
        submission_type = normalize_submission_type(prepared.get("submissionType"))
        base = safe_government_submission_summary(prepared)
        idempotency_key = validate_text("idempotencyKey", prepared.get("idempotencyKey"), max_len=300)
        if submission_type == "tra":
            outcome = call_tra_submitter(config, prepared)
            if outcome.get("status") == "submitted" and outcome.get("receiptReference"):
                record_result = record_government_submission(
                    config,
                    registration_id=registration_id,
                    submission_type=submission_type,
                    state="submitted",
                    receipt_reference=str(outcome["receiptReference"]),
                    idempotency_key=idempotency_key,
                    payload_summary={
                        "submissionType": submission_type,
                        "guestCount": base.get("guestCount"),
                        "reservationId": reservation_id,
                    },
                    response_summary=outcome.get("responseSummary") if isinstance(outcome.get("responseSummary"), dict) else {},
                )
                results.append({
                    **base,
                    "submitStatus": "submitted",
                    "receiptReference": outcome["receiptReference"],
                    "recorded": staff_safe_value(record_result),
                })
                continue
            overall_status = "blocked" if outcome.get("status") == "blocked" else "partial_failure"
            if outcome.get("status") == "failed":
                record_government_submission(
                    config,
                    registration_id=registration_id,
                    submission_type=submission_type,
                    state="failed",
                    idempotency_key=idempotency_key,
                    payload_summary={
                        "submissionType": submission_type,
                        "guestCount": base.get("guestCount"),
                        "reservationId": reservation_id,
                    },
                    response_summary=outcome.get("responseSummary") if isinstance(outcome.get("responseSummary"), dict) else {},
                    error_code=str(outcome.get("reason") or "submit_failed"),
                    error_message="TRA submission did not return a verified receipt/reference.",
                )
            results.append({
                **base,
                "submitStatus": outcome.get("status") or "failed",
                "reason": outcome.get("reason") or "submit_failed",
                "missingFields": staff_safe_value(outcome.get("missingFields")),
            })
            continue

        outcome = call_sire_submitter(config, prepared, submission_type)
        if outcome.get("status") == "submitted" and outcome.get("receiptReference"):
            record_result = record_government_submission(
                config,
                registration_id=registration_id,
                submission_type=submission_type,
                state="submitted",
                receipt_reference=str(outcome["receiptReference"]),
                idempotency_key=idempotency_key,
                payload_summary={
                    "submissionType": submission_type,
                    "guestCount": base.get("guestCount"),
                    "reservationId": reservation_id,
                    **(outcome.get("payloadSummary") if isinstance(outcome.get("payloadSummary"), dict) else {}),
                },
                response_summary=outcome.get("responseSummary") if isinstance(outcome.get("responseSummary"), dict) else {},
            )
            results.append({
                **base,
                "submitStatus": "submitted",
                "receiptReference": outcome["receiptReference"],
                "recorded": staff_safe_value(record_result),
            })
            continue
        overall_status = "blocked" if outcome.get("status") == "blocked" else "partial_failure"
        if outcome.get("status") == "failed":
            record_government_submission(
                config,
                registration_id=registration_id,
                submission_type=submission_type,
                state="failed",
                idempotency_key=idempotency_key,
                payload_summary={
                    "submissionType": submission_type,
                    "guestCount": base.get("guestCount"),
                    "reservationId": reservation_id,
                    **(outcome.get("payloadSummary") if isinstance(outcome.get("payloadSummary"), dict) else {}),
                },
                response_summary=outcome.get("responseSummary") if isinstance(outcome.get("responseSummary"), dict) else {},
                error_code=str(outcome.get("reason") or "submit_failed"),
                error_message="SIRE submission did not return a verified receipt/reference.",
            )
        results.append({
            **base,
            "submitStatus": outcome.get("status") or "failed",
            "reason": outcome.get("reason") or "submit_failed",
            "missingFields": staff_safe_value(outcome.get("missingFields")),
            "payloadSummary": staff_safe_value(outcome.get("payloadSummary")),
        })

    return {
        "ok": True,
        "reservationId": reservation_id,
        "registrationId": registration_id,
        "mode": mode,
        "status": overall_status,
        "results": results,
        "warnings": [
            "Only results with a receiptReference were recorded as submitted in PMS.",
            "SIRE live submission requires a verified SIRE adapter endpoint or browser routine.",
        ],
    }


def pending_registro_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("registrations", "items", "pending", "records", "data"):
            child = value.get(key)
            if isinstance(child, list):
                return [item for item in child if isinstance(item, dict)]
            if isinstance(child, dict):
                nested = pending_registro_items(child)
                if nested:
                    return nested
    return []


def registro_pickup_label(item: dict[str, Any]) -> str:
    return validate_text(
        "registroPickupLabel",
        first_present(item.get("guestName"), item.get("bookingCode"), item.get("reservationId"), item.get("registrationId"), "Registro"),
        max_len=120,
    ) or "Registro"


def summarize_registro_pickup_result(result: dict[str, Any]) -> str:
    label = result.get("label") or "Registro"
    status = result.get("status")
    if status == "submitted":
        reference = result.get("receiptReference")
        submitted_types = result.get("submittedTypes") if isinstance(result.get("submittedTypes"), list) else []
        type_label = ", ".join(str(item) for item in submitted_types) if submitted_types else "gobierno"
        return f"- {label}: enviado {type_label}" + (f" (ref. {reference})" if reference else "")
    if status == "no_government_due":
        due = result.get("dueSubmissionTypes") or []
        if due:
            return f"- {label}: nada enviado; pendiente {', '.join(str(item) for item in due)}"
        return f"- {label}: nada pendiente"
    if status == "needs_review":
        reason = result.get("reason") or "requiere revision en PMS"
        return f"- {label}: necesita revision ({reason})"
    if status == "blocked":
        reason = result.get("reason") or "bloqueado"
        return f"- {label}: bloqueado ({reason})"
    if status == "error":
        reason = result.get("reason") or "error"
        return f"- {label}: error ({reason})"
    return f"- {label}: {status or 'revisado'}"


def build_registro_pickup_message(summary: dict[str, Any]) -> str:
    lines = ["Registro pickup diario"]
    processed = summary.get("processed") if isinstance(summary.get("processed"), list) else []
    review = summary.get("needsReview") if isinstance(summary.get("needsReview"), list) else []
    skipped = summary.get("skipped") if isinstance(summary.get("skipped"), list) else []
    errors = summary.get("errors") if isinstance(summary.get("errors"), list) else []

    if processed:
        lines.append("")
        lines.append("Procesados")
        lines.extend(summarize_registro_pickup_result(item) for item in processed)
    if review:
        lines.append("")
        lines.append("Necesitan revision")
        lines.extend(summarize_registro_pickup_result(item) for item in review)
    if errors:
        lines.append("")
        lines.append("Errores")
        lines.extend(summarize_registro_pickup_result(item) for item in errors)
    if not processed and not review and not errors:
        lines.append("")
        lines.append("No hay registros pendientes para TRA/SIRE.")
    if skipped:
        lines.append("")
        lines.append("Omitidos")
        lines.extend(summarize_registro_pickup_result(item) for item in skipped)
    return "\n".join(lines)


def registro_pickup_in_window(item: dict[str, Any], start_date: str, end_date: str) -> bool:
    arrival = date_part(item.get("arrivalDate"))
    departure = date_part(item.get("departureDate"))
    if not arrival and not departure:
        return False
    if arrival and start_date <= arrival <= end_date:
        return True
    if departure and start_date <= departure <= end_date:
        return True
    if arrival and departure and arrival < start_date and departure > start_date:
        return True
    return False


def tool_hotel_registro_daily_pickup(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    submit_government = validate_bool("submitTra", args.get("submitTra"))
    if submit_government is None:
        submit_government = True
    notify = validate_bool("notify", args.get("notify"))
    if notify is None:
        notify = False
    max_records = validate_int("maxRecords", args.get("maxRecords"), min_value=1, max_value=100) or 25
    days_back = validate_int("daysBack", args.get("daysBack"), min_value=0, max_value=30)
    if days_back is None:
        days_back = 1
    days_ahead = validate_int("daysAhead", args.get("daysAhead"), min_value=0, max_value=30)
    if days_ahead is None:
        days_ahead = 2
    start_date = local_date(config, -days_back)
    end_date = local_date(config, days_ahead)

    pending_raw = pms_tool(config, "registro_list_pending", {}, profile="registro_read")
    all_items = pending_registro_items(pending_raw)
    items = [item for item in all_items if registro_pickup_in_window(item, start_date, end_date)]
    summary: dict[str, Any] = {
        "ok": True,
        "mode": "submit_government" if submit_government else "dry_run",
        "window": {"startDate": start_date, "endDate": end_date},
        "pendingCount": len(all_items),
        "eligibleCount": len(items),
        "processed": [],
        "needsReview": [],
        "skipped": [],
        "errors": [],
    }

    for item in items[:max_records]:
        label = registro_pickup_label(item)
        registration_id = validate_safe_id("registrationId", item.get("registrationId"), required=False)
        reservation_id = validate_safe_id("reservationId", item.get("reservationId"), required=False)
        due_types = item.get("dueSubmissionTypes") if isinstance(item.get("dueSubmissionTypes"), list) else []
        document_count = validate_int("documentCount", item.get("documentCount"), min_value=0, max_value=100) or 0
        status = str(item.get("status") or "").lower()
        base = {
            "label": label,
            "registrationId": registration_id,
            "reservationId": reservation_id,
            "statusBefore": status,
            "documentCount": document_count,
            "dueSubmissionTypes": staff_safe_value(due_types),
        }
        try:
            if not registration_id:
                summary["needsReview"].append({**base, "status": "needs_review", "reason": "sin registro"})
                continue
            requested_types = [item for item in due_types if normalize_submission_type(item) in REGISTRO_SUBMISSION_TYPES]
            if not requested_types and status == "validated":
                summary["skipped"].append({**base, "status": "no_government_due"})
                continue
            if status != "validated":
                if document_count <= 0:
                    summary["needsReview"].append({**base, "status": "needs_review", "reason": "sin documentos"})
                    continue
                if not reservation_id:
                    summary["needsReview"].append({**base, "status": "needs_review", "reason": "sin reserva vinculada"})
                    continue
                extraction = tool_hotel_registro_extract_reservation({"reservationId": reservation_id, "record": True})
                base["extractionStatus"] = extraction.get("status") or "processed"
                base["guestCount"] = extraction.get("guestCount")
                review_count = sum(1 for row in extraction.get("results") or [] if isinstance(row, dict) and row.get("status") == "needs_review")
                if review_count:
                    summary["needsReview"].append({**base, "status": "needs_review", "reason": f"{review_count} huesped(es) necesitan revision"})
                    continue
            submit_args = {
                "registrationId": registration_id,
                "submissionTypes": requested_types,
                "mode": "submit" if submit_government else "dry_run",
            }
            if reservation_id:
                submit_args["reservationId"] = reservation_id
            submit_result = tool_hotel_registro_submit_government(submit_args)
            submit_status = submit_result.get("status")
            first_result = None
            for row in submit_result.get("results") or []:
                if isinstance(row, dict):
                    first_result = row
                    break
            if submit_status == "submitted":
                submitted_types = [
                    row.get("submissionType")
                    for row in submit_result.get("results") or []
                    if isinstance(row, dict) and row.get("submitStatus") == "submitted"
                ]
                summary["processed"].append({
                    **base,
                    "status": "submitted",
                    "submittedTypes": submitted_types,
                    "receiptReference": (first_result or {}).get("receiptReference"),
                })
            elif submit_status in {"ready"}:
                summary["processed"].append({**base, "status": "ready"})
            elif submit_status in {"no_due"}:
                summary["skipped"].append({**base, "status": "no_government_due"})
            else:
                reason = (first_result or {}).get("reason") or submit_status or "no listo"
                target = summary["needsReview"] if submit_status in {"needs_info", "blocked"} else summary["errors"]
                target.append({**base, "status": "needs_review" if target is summary["needsReview"] else "error", "reason": reason})
        except ToolError as exc:
            summary["errors"].append({**base, "status": "error", "reason": exc.code})

    if len(items) > max_records:
        summary["truncated"] = True
        summary["remainingCount"] = len(items) - max_records

    message = build_registro_pickup_message(summary)
    summary["message"] = message
    if notify:
        summary["telegram"] = tool_hotel_telegram_send_message({"text": message})
    return summary


def tool_hotel_registro_record_submission_status(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    registration_id = validate_safe_id("registrationId", args.get("registrationId"))
    submission_type = normalize_submission_type(args.get("submissionType"))
    state = validate_submission_state(args.get("state"))
    note = staff_safe_note(args.get("note"), max_len=1000)
    payload = clean_payload({
        "registrationId": registration_id,
        "submissionType": submission_type,
        "state": state,
        "attemptedAt": now_iso(),
        "errorCode": validate_text("errorCode", args.get("errorCode"), max_len=120),
        "errorMessage": validate_text("errorMessage", args.get("errorMessage"), max_len=1000),
        "payloadSummary": clean_payload({
            "source": "hotel_registro_record_submission_status",
            "note": note,
        }),
        "responseSummary": clean_payload({
            "recordedBy": "hotel_agent",
            "governmentSubmitted": False,
        }),
    })
    result = pms_tool(config, "registro_record_submission", payload, profile="registro_write")
    return {"ok": True, "submission": staff_safe_value(result)}


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
    "hotel_pms_list_reservations": (
        "Search/list PMS reservations with staff-safe operational fields and no finance amounts.",
        {
            "type": "object",
            "properties": {
                "search": {"type": ["string", "null"]},
                "source": {"type": ["string", "null"]},
                "status": {"type": ["string", "null"]},
                "dateFrom": {"type": ["string", "null"]},
                "dateTo": {"type": ["string", "null"]},
                "limit": {"type": ["integer", "null"]},
            },
            "additionalProperties": False,
        },
        tool_hotel_pms_list_reservations,
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
        "Get read-only staff-safe PMS reservation context, checklist, and operational metadata.",
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
    "hotel_pms_list_booking_revisions": (
        "List PMS booking/channel revision inbox rows.",
        {
            "type": "object",
            "properties": {
                "processingStatus": {"type": ["string", "null"]},
                "ackStatus": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        tool_hotel_pms_list_booking_revisions,
    ),
    "hotel_pms_list_sync_events": (
        "List PMS sync/channel events with optional status, direction, or resource type filters.",
        {
            "type": "object",
            "properties": {
                "status": {"type": ["string", "null"]},
                "direction": {"type": ["string", "null"]},
                "resourceType": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        tool_hotel_pms_list_sync_events,
    ),
    "hotel_pms_get_mapping_status": (
        "Get PMS channel/entity mapping status.",
        {"type": "object", "properties": {"entityType": {"type": ["string", "null"]}}, "additionalProperties": False},
        tool_hotel_pms_get_mapping_status,
    ),
    "hotel_pms_get_ari_outbox_health": (
        "Get PMS channel manager outbound queue health.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        tool_hotel_pms_get_ari_outbox_health,
    ),
    "hotel_pms_prepare_reservation": (
        "Prepare and validate a PMS reservation from normalized staff intent. No PMS reservation is created.",
        {
            "type": "object",
            "properties": {
                "bookingType": {"type": ["string", "null"]},
                "guestName": {"type": ["string", "null"]},
                "guestEmail": {"type": ["string", "null"]},
                "guestPhone": {"type": ["string", "null"]},
                "operatorName": {"type": ["string", "null"]},
                "sourceText": {"type": ["string", "null"]},
                "source": {"type": ["string", "null"]},
                "commercialTrack": {"type": ["string", "null"]},
                "payerResponsibility": {"type": ["string", "null"]},
                "sourceReference": {"type": ["string", "null"]},
                "arrivalDate": {"type": ["string", "null"]},
                "departureDate": {"type": ["string", "null"]},
                "visitDate": {"type": ["string", "null"]},
                "adultsCount": {"type": ["integer", "null"]},
                "childrenCount": {"type": ["integer", "null"]},
                "infantsCount": {"type": ["integer", "null"]},
                "unitAllocations": {
                    "type": ["array", "null"],
                    "items": {
                        "type": "object",
                        "properties": {
                            "unitCode": {"type": ["string", "null"]},
                            "quantity": {"type": ["integer", "null"]},
                            "label": {"type": ["string", "null"]},
                        },
                        "additionalProperties": False,
                    },
                },
                "expectedArrivalTime": {"type": ["string", "null"]},
                "transportRequested": {"type": ["boolean", "null"]},
                "dietaryNotes": {"type": ["string", "null"]},
                "specialRequests": {"type": ["string", "null"]},
                "internalNotes": {"type": ["string", "null"]},
                "linkedActivities": {
                    "type": ["array", "null"],
                    "items": {
                        "type": "object",
                        "properties": {
                            "bookingType": {"type": ["string", "null"]},
                            "date": {"type": ["string", "null"]},
                            "participants": {"type": ["integer", "null"]},
                            "notes": {"type": ["string", "null"]},
                        },
                        "additionalProperties": False,
                    },
                },
                "sourceMetadata": {"type": ["object", "null"]},
            },
            "additionalProperties": False,
        },
        tool_hotel_pms_prepare_reservation,
    ),
    "hotel_pms_create_reservation": (
        "Create a PMS reservation from a pending PMS-prepared draft after staff replies si, or from a legacy confirmation code.",
        {
            "type": "object",
            "properties": {
                "pendingId": {"type": ["string", "null"]},
                "confirmationText": {"type": ["string", "null"]},
                "confirmationCode": {"type": ["string", "null"]},
                "idempotencyKey": {"type": ["string", "null"]},
                "sourceMetadata": {"type": ["object", "null"]},
            },
            "additionalProperties": False,
        },
        tool_hotel_pms_create_reservation,
    ),
    "hotel_registro_get_by_reservation": (
        "Read the Registro record for a PMS reservation without exposing document bytes or fetch tokens.",
        {"type": "object", "properties": {"reservationId": {"type": "string"}}, "required": ["reservationId"], "additionalProperties": False},
        tool_hotel_registro_get_by_reservation,
    ),
    "hotel_registro_list_guests": (
        "List structured Registro guests for a registration.",
        {"type": "object", "properties": {"registrationId": {"type": "string"}}, "required": ["registrationId"], "additionalProperties": False},
        tool_hotel_registro_list_guests,
    ),
    "hotel_registro_list_documents": (
        "List Registro document metadata for a registration or guest without exposing document bytes or fetch tokens.",
        {
            "type": "object",
            "properties": {
                "registrationId": {"type": "string"},
                "registrationGuestId": {"type": ["string", "null"]},
            },
            "required": ["registrationId"],
            "additionalProperties": False,
        },
        tool_hotel_registro_list_documents,
    ),
    "hotel_registro_extract_reservation": (
        "Fetch each guest document through scoped PMS Registro tokens, extract identity fields with vision, and record guest-level extraction results in PMS.",
        {
            "type": "object",
            "properties": {
                "reservationId": {"type": "string"},
                "record": {"type": ["boolean", "null"]},
            },
            "required": ["reservationId"],
            "additionalProperties": False,
        },
        tool_hotel_registro_extract_reservation,
    ),
    "hotel_registro_prepare_submissions": (
        "Check whether a PMS Registro record is ready for TRA/SIRE submission and return a staff-safe staged plan. Does not submit to government systems.",
        {
            "type": "object",
            "properties": {
                "reservationId": {"type": "string"},
                "submissionTypes": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
            },
            "required": ["reservationId"],
            "additionalProperties": False,
        },
        tool_hotel_registro_prepare_submissions,
    ),
    "hotel_registro_prepare_government_submission": (
        "Ask PMS to prepare official TRA/SIRE payloads and return only safe readiness metadata. Does not expose government payloads or submit them.",
        {
            "type": "object",
            "properties": {
                "reservationId": {"type": ["string", "null"]},
                "registrationId": {"type": ["string", "null"]},
                "submissionTypes": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
            },
            "additionalProperties": False,
        },
        tool_hotel_registro_prepare_government_submission,
    ),
    "hotel_registro_submit_government": (
        "Dry-run or receipt-gated submit PMS-prepared Registro payloads to configured government adapters. Returns no identity payloads.",
        {
            "type": "object",
            "properties": {
                "reservationId": {"type": ["string", "null"]},
                "registrationId": {"type": ["string", "null"]},
                "submissionTypes": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                },
                "mode": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        tool_hotel_registro_submit_government,
    ),
    "hotel_registro_daily_pickup": (
        "Process pending PMS Registro records: extract uploaded documents, submit TRA when ready, and optionally send a staff-safe Telegram summary.",
        {
            "type": "object",
            "properties": {
                "submitTra": {"type": ["boolean", "null"]},
                "notify": {"type": ["boolean", "null"]},
                "maxRecords": {"type": ["integer", "null"]},
                "daysBack": {"type": ["integer", "null"]},
                "daysAhead": {"type": ["integer", "null"]},
            },
            "additionalProperties": False,
        },
        tool_hotel_registro_daily_pickup,
    ),
    "hotel_registro_record_submission_status": (
        "Record a pending, failed, or needs-info TRA/SIRE submission attempt in PMS. This tool cannot mark government submissions as submitted.",
        {
            "type": "object",
            "properties": {
                "registrationId": {"type": "string"},
                "submissionType": {"type": "string"},
                "state": {"type": "string"},
                "note": {"type": ["string", "null"]},
                "errorCode": {"type": ["string", "null"]},
                "errorMessage": {"type": ["string", "null"]},
            },
            "required": ["registrationId", "submissionType", "state"],
            "additionalProperties": False,
        },
        tool_hotel_registro_record_submission_status,
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
