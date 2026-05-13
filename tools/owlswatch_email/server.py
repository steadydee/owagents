#!/usr/bin/env python3
"""Narrow tools for Owl's Watch email triage and draft preparation.

Correo is a drafting clerk. This server keeps Gmail, Luna, Telegram, and local
task side effects behind structured tools so the model never receives raw
tokens or broad filesystem/network authority.
"""

from __future__ import annotations

import base64
import datetime as dt
import email.utils
import hashlib
import hmac
import html
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable

WORKSPACE = Path(os.environ.get("OWLSWATCH_EMAIL_WORKSPACE", "~/.openclaw/workspace-owlswatch-correo")).expanduser()
CONFIG_PATH = Path(os.environ.get("OPENCLAW_CONFIG_PATH", "~/.openclaw-owlswatch/openclaw.json")).expanduser()
TASK_DIR = WORKSPACE / "tasks" / "email"
MEMORY_DIR = WORKSPACE / "memory"

DEFAULT_LUNA_BASE_URL = "https://luna.owlswatch.com"
DEFAULT_OPERATIONS_BASE_URL = "https://operations.owlswatch.com"
DEFAULT_NOTIFY_CHAT_ID = "-1003949383737"
DEFAULT_GMAIL_ACCOUNT = "info@owlswatch.com"

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/+\\-]{1,300}$")
TEXT_RE = re.compile(r"^[\s\S]{0,50000}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LOW_VALUE_MARKERS = (
    "unsubscribe",
    "newsletter",
    "promotion",
    "promo",
    "noreply",
    "no-reply",
    "no responder",
    "no-responder",
    "donotreply",
    "do-not-reply",
    "notificaciones",
    "notification",
    "google alerts",
    "cierre de ventas",
    "compra por $",
    "link de pago bold",
    "bold",
    "factura electrónica",
    "factura electronica",
    "documento electrónico",
    "documento electronico",
    "efactura",
    "dian",
    "little hotelier",
    "booking for",
    "thebookingbutton",
    "bookingbutton",
    "mail-cotelco.org",
    "cotelco - comunicaciones",
    "cotelco - estudios",
    "programa de formación",
    "programa de formacion",
    "monitor del mercado laboral",
    "afiliados cotelco",
    "canceled event",
    "cancelled event",
    "this event has been canceled",
    "this event has been cancelled",
    "google meet",
)
STAFF_DOMAINS = ("owlswatch.com",)


class ToolError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat().replace("+00:00", "Z")


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


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
    mcp_env = config.get("mcp", {}).get("servers", {}).get("owlswatch_email", {}).get("env", {})
    value = mcp_env.get(key)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    env_vars = config.get("env", {}).get("vars", {})
    value = env_vars.get(key)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    email_cfg = config.get("emailAgent", {})
    candidate = key.lower().split("_")
    camel = candidate[0] + "".join(part.title() for part in candidate[1:])
    value = email_cfg.get(camel)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    return None


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


def gmail_account(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "OWLSWATCH_GMAIL_ACCOUNT") or DEFAULT_GMAIL_ACCOUNT
    if not EMAIL_RE.match(raw):
        raise ToolError("config_invalid", "Configured Gmail account is malformed.")
    return raw


def google_credentials_path(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "GOOGLE_APPLICATION_CREDENTIALS")
    if not raw:
        raw = config.get("googleDrive", {}).get("serviceAccountCredentials")
    if not isinstance(raw, str) or not raw or raw.startswith("<"):
        raise ToolError("config_missing", "Google service account credentials are missing from tool environment/config.")
    return str(Path(raw).expanduser())


def google_build_service(config: dict[str, Any], scopes: list[str]) -> Any:
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except Exception as exc:
        raise ToolError("dependency_missing", "Google API client libraries are not installed for the email tools.") from exc
    credentials = service_account.Credentials.from_service_account_file(google_credentials_path(config), scopes=scopes)
    credentials = credentials.with_subject(gmail_account(config))
    return build("gmail", "v1", credentials=credentials)


def luna_base_url(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "LUNA_BASE_URL") or DEFAULT_LUNA_BASE_URL
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("config_invalid", "Luna base URL must be an https URL.")
    return raw.rstrip("/")


def luna_secret(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "OW_AGENT_TOKEN_SECRET")
    if not raw:
        raise ToolError("config_missing", "Luna machine-token secret is missing from tool environment/config.")
    return raw


def operations_base_url(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "OPERATIONS_BASE_URL") or cfg_env(config, "OPERATIONS_API_BASE_URL") or DEFAULT_OPERATIONS_BASE_URL
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("config_invalid", "Operations base URL must be an https URL.")
    return raw.rstrip("/")


def operations_email_token(config: dict[str, Any]) -> str:
    token = cfg_env(config, "EMAIL_AGENT_API_TOKEN")
    if token:
        return token
    token_file = cfg_env(config, "EMAIL_AGENT_API_TOKEN_FILE")
    if token_file:
        try:
            token = Path(token_file).expanduser().read_text().strip()
        except FileNotFoundError as exc:
            raise ToolError("config_missing", "Operations Email Desk agent token file is missing from runtime storage.") from exc
        if token:
            return token
    raise ToolError("config_missing", "Operations Email Desk agent token is missing from tool environment/config.")


def telegram_token(config: dict[str, Any]) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    token = config.get("channels", {}).get("telegram", {}).get("botToken")
    if isinstance(token, str) and token and not token.startswith("<"):
        return token
    raise ToolError("config_missing", "Telegram bot token is missing from tool environment/config.")


def email_notify_chat_id(config: dict[str, Any]) -> str:
    return cfg_env(config, "OWLSWATCH_EMAIL_NOTIFY_CHAT_ID") or DEFAULT_NOTIFY_CHAT_ID


def email_notify_thread_id(config: dict[str, Any]) -> str | None:
    return cfg_env(config, "OWLSWATCH_EMAIL_NOTIFY_THREAD_ID")


def gmail_drafts_enabled(config: dict[str, Any]) -> bool:
    value = cfg_env(config, "OWLSWATCH_GMAIL_DRAFTS_ENABLED")
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"[ \t]+", " ", text)).strip()


def decode_gmail_body_data(value: str | None) -> str:
    if not value:
        return ""
    padded = value + ("=" * (-len(value) % 4))
    try:
        return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_gmail_body(payload: dict[str, Any]) -> str:
    plain: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType")
        body_data = (part.get("body") or {}).get("data")
        if mime == "text/plain":
            plain.append(decode_gmail_body_data(body_data))
        elif mime == "text/html":
            html_parts.append(strip_html(decode_gmail_body_data(body_data)))
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                walk(child)

    walk(payload)
    text = "\n\n".join(p for p in plain if p.strip()) or "\n\n".join(p for p in html_parts if p.strip())
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:30000]


def headers_map(message: dict[str, Any]) -> dict[str, str]:
    headers = (message.get("payload") or {}).get("headers") or []
    return {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in headers if isinstance(h, dict)}


def parsed_email_address(value: str) -> str:
    return email.utils.parseaddr(value or "")[1].lower()


def parsed_email_name(value: str) -> str | None:
    name = email.utils.parseaddr(value or "")[0].strip().strip('"')
    return name or None


def parsed_email_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [addr.lower() for _, addr in email.utils.getaddresses([value]) if addr]


def message_to_summary(message: dict[str, Any]) -> dict[str, Any]:
    headers = headers_map(message)
    internal_ms = int(message.get("internalDate") or 0)
    date_iso = dt.datetime.fromtimestamp(internal_ms / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z") if internal_ms else None
    return {
        "id": message.get("id"),
        "threadId": message.get("threadId"),
        "from": headers.get("from"),
        "fromName": parsed_email_name(headers.get("from", "")),
        "fromEmail": parsed_email_address(headers.get("from", "")),
        "to": headers.get("to"),
        "toEmails": parsed_email_list(headers.get("to")),
        "ccEmails": parsed_email_list(headers.get("cc")),
        "subject": headers.get("subject", ""),
        "date": date_iso,
        "messageIdHeader": headers.get("message-id"),
        "bodyText": extract_gmail_body(message.get("payload") or {}),
        "labelIds": message.get("labelIds") or [],
    }


def gmail_source_url(thread_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#inbox/{urllib.parse.quote(thread_id)}"


def list_threads(service: Any, query: str, max_results: int) -> list[dict[str, Any]]:
    response = service.users().threads().list(userId="me", q=query, maxResults=max_results).execute()
    return response.get("threads") or []


def get_thread(service: Any, thread_id: str, fmt: str = "full") -> dict[str, Any]:
    return service.users().threads().get(userId="me", id=thread_id, format=fmt).execute()


def get_message(service: Any, message_id: str, fmt: str = "metadata") -> dict[str, Any]:
    return service.users().messages().get(userId="me", id=message_id, format=fmt).execute()


def thread_messages(thread: dict[str, Any]) -> list[dict[str, Any]]:
    messages = [message_to_summary(m) for m in thread.get("messages") or []]
    return sorted(messages, key=lambda m: m.get("date") or "")


def is_low_value_message(message: dict[str, Any]) -> bool:
    haystack = " ".join(str(message.get(k) or "") for k in ("from", "subject", "bodyText")).lower()
    return any(marker in haystack for marker in LOW_VALUE_MARKERS)


def is_staff_sender(sender: str, account: str) -> bool:
    sender = (sender or "").lower()
    account = (account or "").lower()
    if not sender:
        return False
    if sender == account:
        return True
    return any(sender.endswith(f"@{domain}") for domain in STAFF_DOMAINS)


def latest_external_message(messages: list[dict[str, Any]], account: str) -> dict[str, Any] | None:
    for message in reversed(messages):
        sender = str(message.get("fromEmail") or "").lower()
        if sender and not is_staff_sender(sender, account) and not is_low_value_message(message):
            return message
    return None


def latest_meaningful_message(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in reversed(messages):
        if not is_low_value_message(message):
            return message
    return messages[-1] if messages else None


def thread_match_from_thread(thread: dict[str, Any], account: str) -> dict[str, Any] | None:
    messages = thread_messages(thread)
    if not messages:
        return None
    latest = latest_meaningful_message(messages)
    first = messages[0]
    return {
        "threadId": thread.get("id"),
        "sourceUrl": gmail_source_url(str(thread.get("id"))),
        "subject": latest.get("subject") or first.get("subject"),
        "from": latest.get("from"),
        "fromEmail": latest.get("fromEmail"),
        "date": latest.get("date"),
        "snippet": thread.get("snippet"),
        "messageCount": len(messages),
        "latestExternalFrom": (latest_external_message(messages, account) or {}).get("from"),
    }


def default_recent_query(hours: int) -> str:
    if hours <= 24:
        return 'newer_than:1d -category:promotions -category:social -in:spam -in:trash'
    days = max(1, (hours + 23) // 24)
    return f'newer_than:{days}d -category:promotions -category:social -in:spam -in:trash'


def extract_gmail_url_token(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc not in {"mail.google.com", "www.mail.google.com"}:
        raise ToolError("invalid_input", "url must be a Gmail web URL.")
    candidates: list[str] = []
    for value in (parsed.fragment, parsed.path, parsed.query):
        candidates.extend(part for part in re.split(r"[/=&?]+", value) if part)
    for candidate in reversed(candidates):
        if re.match(r"^[A-Za-z0-9_-]{10,300}$", candidate):
            return candidate
    return None


def task_path(task_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", task_id)[:180]
    return TASK_DIR / f"{safe}.json"


def task_id_from(payload: dict[str, Any]) -> str:
    explicit = payload.get("taskId") or payload.get("task_id")
    if explicit:
        return validate_safe_id("taskId", explicit) or ""
    base = payload.get("gmailThreadId") or payload.get("threadId") or payload.get("sourceUrl") or json_dumps(payload)
    digest = hashlib.sha256(str(base).encode()).hexdigest()[:16]
    return f"email-{digest}"


def read_task(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def write_task(task: dict[str, Any]) -> None:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    task_path(str(task["taskId"])).write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n")


def sign_luna_token(config: dict[str, Any]) -> str:
    now = int(time.time())
    payload = {
        "typ": "agent_access",
        "iss": "owhub",
        "aud": "luna",
        "propertyIds": ["owlswatch"],
        "activePropertyId": "owlswatch",
        "permissions": ["luna.read"],
        "allowedToolClassifications": ["read"],
        "allowedTools": ["get_email_response_context"],
        "iat": now,
        "exp": now + 300,
        "jti": hashlib.sha256(f"{now}-{os.getpid()}".encode()).hexdigest()[:24],
    }
    encoded = b64url(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(luna_secret(config).encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{b64url(signature)}"


def http_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 30) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        detail = re.sub(r"\s+", " ", body).strip()[:500]
        message = f"Upstream request failed with HTTP {exc.code}."
        if detail:
            message = f"{message} {detail}"
        raise ToolError("http_error", message, retryable=500 <= exc.code < 600) from exc
    except urllib.error.URLError as exc:
        raise ToolError("network_error", "Network request failed.", retryable=True) from exc


def operations_post(config: dict[str, Any], path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return http_json(
        f"{operations_base_url(config)}{path}",
        payload,
        {"Authorization": f"Bearer {operations_email_token(config)}"},
        timeout=30,
    )


def list_from_maybe(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def infer_signature_name(body: str | None) -> str | None:
    if not body:
        return None
    lines = [line.strip() for line in body.replace("\r", "").split("\n") if line.strip()]
    for line in reversed(lines[-6:]):
        clean = re.sub(r"^(thanks|thank you|best|regards|kind regards|sincerely|saludos|gracias)[,!. ]*$", "", line, flags=re.I).strip()
        if not clean:
            continue
        if 1 <= len(clean) <= 80 and re.match(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ .'-]{0,79}$", clean):
            if "@" not in clean and not clean.lower().startswith(("http", "www.")):
                return clean
    return None


def message_to_operations_snapshot(message: dict[str, Any], account: str) -> dict[str, Any]:
    sender = str(message.get("fromEmail") or "")
    return {
        "gmailMessageId": message.get("id"),
        "rfc822MessageId": message.get("messageIdHeader"),
        "direction": "staff" if is_staff_sender(sender, account) else "external",
        "fromName": message.get("fromName"),
        "fromEmail": sender,
        "toAddresses": message.get("toEmails") or list_from_maybe(message.get("to")),
        "ccAddresses": message.get("ccEmails") or [],
        "bccAddresses": [],
        "subject": message.get("subject"),
        "snippet": (message.get("bodyText") or "")[:500],
        "bodyText": message.get("bodyText"),
        "sentAt": message.get("date"),
        "hasAttachments": False,
        "attachments": [],
    }


def compact_summary_from_message(message: dict[str, Any] | None) -> str | None:
    if not message:
        return None
    body = re.sub(r"\s+", " ", str(message.get("bodyText") or "")).strip()
    if not body:
        return None
    return body[:500]


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and not value:
        return True
    return False


def set_if_blank(target: dict[str, Any], key: str, value: Any) -> None:
    if is_blank(target.get(key)) and not is_blank(value):
        target[key] = value


def normalize_operations_intake_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("gmail"), dict) and isinstance(payload.get("thread"), dict) and isinstance(payload.get("draft"), dict):
        return payload

    draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else {}
    thread_id = payload.get("threadId") or payload.get("gmailThreadId") or payload.get("gmail_thread_id")
    if not thread_id:
        return payload

    from_email = payload.get("clientEmail") or payload.get("from") or payload.get("fromEmail")
    client_name = payload.get("customerName") or payload.get("clientName")
    subject = payload.get("subject") or draft.get("subject")
    summary = payload.get("summary") or payload.get("originalSummary")
    status = payload.get("status") or draft.get("status") or "draft_ready"
    body = draft.get("body") or payload.get("draftBody") or payload.get("body")
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []

    return {
        "propertyId": payload.get("propertyId") or "owlswatch",
        "agentId": payload.get("agentId") or payload.get("createdBy") or "correo",
        "sourceApp": payload.get("sourceApp") or "email_agent",
        "gmail": {
            "account": payload.get("gmailAccount") or DEFAULT_GMAIL_ACCOUNT,
            "threadId": str(thread_id),
            "sourceMessageId": payload.get("gmailSourceMessageId") or payload.get("sourceMessageId") or str(thread_id),
            "lastMessageId": payload.get("gmailLastMessageId") or payload.get("lastMessageId") or str(thread_id),
        },
        "thread": {
            "subject": subject,
            "clientName": client_name,
            "clientEmail": from_email,
            "participants": payload.get("participants") or [{"name": client_name, "email": from_email, "role": "external"}],
            "detectedLanguage": payload.get("detectedLanguage") or payload.get("language") or "en",
            "category": payload.get("category") or "new_guest_inquiry",
            "priority": payload.get("priority") or "normal",
            "lastExternalMessageAt": payload.get("lastExternalMessageAt"),
            "lastStaffMessageAt": payload.get("lastStaffMessageAt"),
            "summary": summary,
            "messages": messages,
        },
        "draft": {
            "status": status,
            "confidence": payload.get("confidence") or "medium",
            "detectedLanguage": payload.get("detectedLanguage") or payload.get("language") or "en",
            "toAddresses": list_from_maybe(draft.get("to") or draft.get("toAddresses") or from_email),
            "ccAddresses": list_from_maybe(draft.get("cc") or draft.get("ccAddresses")),
            "bccAddresses": list_from_maybe(draft.get("bcc") or draft.get("bccAddresses")),
            "subject": subject,
            "body": body,
        },
        "context": {
            "originalClientQuestion": payload.get("originalClientQuestion") or summary,
            "missingInformationFlags": payload.get("missingInformationFlags") or [],
            "warningFlags": payload.get("warningFlags") or [],
            "boundaries": payload.get("boundaries") or payload.get("reviewNotes") or [],
            "lunaRequest": payload.get("lunaRequest") or {},
            "lunaSources": payload.get("lunaSources") or {},
            "lunaContextSummary": payload.get("lunaContextSummary"),
            "quoteId": payload.get("quoteId"),
            "agentNotes": payload.get("agentNotes") or "\n".join(payload.get("reviewNotes") or []),
        },
        "options": {
            "createGmailDraft": False,
            "notifyTelegram": False,
        },
    }


def enrich_operations_intake_payload(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    gmail = payload.get("gmail")
    thread_payload = payload.get("thread")
    draft = payload.get("draft")
    context = payload.get("context")
    if not isinstance(gmail, dict) or not isinstance(thread_payload, dict) or not isinstance(draft, dict):
        return payload
    if not isinstance(context, dict):
        context = {}
        payload["context"] = context

    thread_id = gmail.get("threadId") or gmail.get("thread_id")
    if not thread_id:
        return payload

    try:
        account = gmail_account(config)
        service = google_build_service(config, ["https://www.googleapis.com/auth/gmail.readonly"])
        thread = get_thread(service, str(thread_id))
        messages = thread_messages(thread)
    except Exception:
        return payload

    if not messages:
        return payload

    latest = messages[-1]
    latest_external = latest_external_message(messages, account) or latest
    snapshots = [message_to_operations_snapshot(message, account) for message in messages if message.get("id")]

    gmail.setdefault("account", account)
    gmail.setdefault("sourceMessageId", latest_external.get("id") or latest.get("id"))
    gmail.setdefault("lastMessageId", latest.get("id"))

    set_if_blank(thread_payload, "subject", latest.get("subject") or latest_external.get("subject"))
    set_if_blank(thread_payload, "clientEmail", latest_external.get("fromEmail"))
    set_if_blank(
        thread_payload,
        "clientName",
        latest_external.get("fromName") or infer_signature_name(latest_external.get("bodyText")),
    )
    existing_participants = thread_payload.get("participants")
    has_real_participant = any((item.get("email") or item.get("name")) for item in existing_participants) if isinstance(existing_participants, list) else False
    if not has_real_participant:
        participants: list[dict[str, Any]] = []
        client_email = thread_payload.get("clientEmail")
        if client_email:
            participants.append({"name": thread_payload.get("clientName"), "email": client_email, "role": "external"})
        participants.append({"name": "Owl's Watch", "email": account, "role": "staff"})
        thread_payload["participants"] = participants
    set_if_blank(thread_payload, "lastExternalMessageAt", latest_external.get("date"))
    staff_dates = [m.get("date") for m in messages if is_staff_sender(str(m.get("fromEmail") or ""), account) and m.get("date")]
    if "lastStaffMessageAt" not in thread_payload or thread_payload.get("lastStaffMessageAt") is None:
        thread_payload["lastStaffMessageAt"] = staff_dates[-1] if staff_dates else None
    set_if_blank(thread_payload, "summary", context.get("originalSummary") or compact_summary_from_message(latest_external))
    if not isinstance(thread_payload.get("messages"), list) or not thread_payload.get("messages"):
        thread_payload["messages"] = snapshots

    set_if_blank(draft, "toAddresses", list_from_maybe(thread_payload.get("clientEmail")))
    if "ccAddresses" not in draft:
        draft["ccAddresses"] = []
    if "bccAddresses" not in draft:
        draft["bccAddresses"] = []
    set_if_blank(draft, "subject", f"Re: {thread_payload.get('subject')}" if thread_payload.get("subject") else None)

    set_if_blank(context, "originalClientQuestion", latest_external.get("bodyText"))
    if "lunaSources" not in context:
        context["lunaSources"] = {}
    if not context.get("originalSummary") and thread_payload.get("summary"):
        context["originalSummary"] = thread_payload.get("summary")

    return payload


def tool_gmail_search_recent_threads(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    hours = int(args.get("hours") or 24)
    if not 1 <= hours <= 168:
        raise ToolError("invalid_input", "hours must be between 1 and 168.")
    max_results = int(args.get("maxResults") or 10)
    if not 1 <= max_results <= 25:
        raise ToolError("invalid_input", "maxResults must be between 1 and 25.")
    include_handled = bool(args.get("includeHandled"))
    query = validate_text("query", args.get("query"), max_len=1000) or default_recent_query(hours)
    service = google_build_service(config, ["https://www.googleapis.com/auth/gmail.readonly"])
    account = gmail_account(config)
    matches = []
    for item in list_threads(service, query, max_results):
        thread = get_thread(service, item["id"], fmt="full")
        messages = thread_messages(thread)
        latest = latest_meaningful_message(messages)
        if latest and is_staff_sender(str(latest.get("fromEmail") or ""), account) and not include_handled:
            continue
        match = thread_match_from_thread(thread, account)
        if match and not is_low_value_message({"from": match.get("from"), "subject": match.get("subject"), "bodyText": match.get("snippet")}):
            matches.append(match)
    return {"ok": True, "query": query, "matches": matches}


def tool_gmail_search_unanswered_threads(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    days = int(args.get("days") or 7)
    if not 1 <= days <= 30:
        raise ToolError("invalid_input", "days must be between 1 and 30.")
    max_results = int(args.get("maxResults") or 25)
    if not 1 <= max_results <= 50:
        raise ToolError("invalid_input", "maxResults must be between 1 and 50.")
    query = validate_text("query", args.get("query"), max_len=1000) or f'newer_than:{days}d -category:promotions -category:social -in:spam -in:trash'
    service = google_build_service(config, ["https://www.googleapis.com/auth/gmail.readonly"])
    account = gmail_account(config)
    matches = []
    for item in list_threads(service, query, max_results):
        thread = get_thread(service, item["id"], fmt="full")
        messages = thread_messages(thread)
        latest = latest_meaningful_message(messages)
        if not latest:
            continue
        last_sender = str(latest.get("fromEmail") or "").lower()
        if last_sender and not is_staff_sender(last_sender, account) and not is_low_value_message(latest):
            match = thread_match_from_thread(thread, account)
            if match:
                match["reason"] = "latest_meaningful_message_from_external_sender"
                matches.append(match)
    return {"ok": True, "query": query, "matches": matches}


def tool_gmail_read_thread(args: dict[str, Any]) -> dict[str, Any]:
    thread_id = validate_safe_id("threadId", args.get("threadId"))
    config = load_config()
    service = google_build_service(config, ["https://www.googleapis.com/auth/gmail.readonly"])
    thread = get_thread(service, thread_id)
    messages = thread_messages(thread)
    return {
        "ok": True,
        "thread": {
            "threadId": thread_id,
            "sourceUrl": gmail_source_url(thread_id),
            "messages": messages,
            "latestExternalMessage": latest_external_message(messages, gmail_account(config)),
        },
    }


def tool_gmail_resolve_url(args: dict[str, Any]) -> dict[str, Any]:
    url = validate_text("url", args.get("url"), required=True, max_len=2000) or ""
    token = extract_gmail_url_token(url)
    if not token:
        return {
            "ok": False,
            "error": {
                "code": "gmail_url_not_resolvable",
                "message": "No candidate Gmail id was found in the URL. Search by sender, subject, and date instead.",
                "retryable": False,
            },
        }
    config = load_config()
    service = google_build_service(config, ["https://www.googleapis.com/auth/gmail.readonly"])
    try:
        thread = get_thread(service, token)
        messages = thread_messages(thread)
        return {
            "ok": True,
            "resolution": "thread_id",
            "thread": {
                "threadId": token,
                "sourceUrl": gmail_source_url(token),
                "messages": messages,
                "latestExternalMessage": latest_external_message(messages, gmail_account(config)),
            },
        }
    except Exception:
        pass
    try:
        message = get_message(service, token)
        thread_id = validate_safe_id("threadId", message.get("threadId"))
        thread = get_thread(service, thread_id)
        messages = thread_messages(thread)
        return {
            "ok": True,
            "resolution": "message_id",
            "thread": {
                "threadId": thread_id,
                "sourceUrl": gmail_source_url(thread_id),
                "messages": messages,
                "latestExternalMessage": latest_external_message(messages, gmail_account(config)),
            },
        }
    except Exception:
        return {
            "ok": False,
            "extractedToken": token,
            "error": {
                "code": "gmail_url_not_resolvable",
                "message": "Gmail did not accept the URL token as an API thread or message id. Search by sender, subject, and date instead.",
                "retryable": False,
            },
        }


def tool_luna_get_email_response_context(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    client_question = validate_text("clientQuestion", args.get("clientQuestion"), required=True, max_len=20000)
    language = validate_text("language", args.get("language"), max_len=10)
    topics = args.get("topics") or []
    if not isinstance(topics, list) or not all(isinstance(t, str) and len(t) <= 80 for t in topics):
        raise ToolError("invalid_input", "topics must be an array of short strings.")
    input_payload = {
        "clientQuestion": client_question,
        "factLimit": int(args.get("factLimit") or 12),
        "blockLimit": int(args.get("blockLimit") or 8),
        "mediaLimit": int(args.get("mediaLimit") or 6),
    }
    if language:
        input_payload["language"] = language
    if topics:
        input_payload["topics"] = topics
    response = http_json(
        f"{luna_base_url(config)}/api/tools/get_email_response_context",
        {"input": input_payload, "context": {"requestSource": "internal_agent"}},
        {
            "Authorization": f"Bearer {sign_luna_token(config)}",
            "x-ow-active-property-id": "owlswatch",
            "x-ow-request-source": "internal_agent",
        },
    )
    if response.get("success") is False:
        raise ToolError("luna_error", "Luna context tool returned an error.", retryable=False)
    return {"ok": True, "context": response.get("data"), "correlationId": response.get("correlationId")}


def tool_email_upsert_task(args: dict[str, Any]) -> dict[str, Any]:
    payload = args.get("task")
    if not isinstance(payload, dict):
        raise ToolError("invalid_input", "task must be an object.")
    task_id = task_id_from(payload)
    existing = read_task(task_path(task_id)) or {}
    now = now_iso()
    task = {
        **existing,
        **payload,
        "taskId": task_id,
        "status": validate_text("status", payload.get("status"), max_len=80) or existing.get("status") or "proposed",
        "updatedAt": now,
        "createdAt": existing.get("createdAt") or now,
    }
    write_task(task)
    return {"ok": True, "taskId": task_id, "taskPath": str(task_path(task_id)), "status": task["status"]}


def tool_email_list_open_tasks(args: dict[str, Any]) -> dict[str, Any]:
    statuses = args.get("statuses") or ["draft_ready", "needs_human", "needs_info", "error", "proposed"]
    if not isinstance(statuses, list) or not all(isinstance(s, str) for s in statuses):
        raise ToolError("invalid_input", "statuses must be an array of strings.")
    limit = int(args.get("limit") or 25)
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    tasks = []
    for path in sorted(TASK_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        task = read_task(path)
        if task and task.get("status") in statuses:
            tasks.append(task)
        if len(tasks) >= limit:
            break
    return {"ok": True, "tasks": tasks}


def tool_operations_email_intake(args: dict[str, Any]) -> dict[str, Any]:
    payload = args.get("payload")
    if not isinstance(payload, dict):
        raise ToolError("invalid_input", "payload must be an object.")
    config = load_config()
    payload = normalize_operations_intake_payload(payload)
    payload = enrich_operations_intake_payload(config, payload)
    data = operations_post(config, "/api/emails/intake", payload)
    if data.get("success") is False or data.get("ok") is False:
        raise ToolError("operations_error", "Operations Email Desk intake returned an error.", retryable=False)
    result = data.get("data") if isinstance(data.get("data"), dict) else data
    return {
        "ok": True,
        "taskId": result.get("taskId") or result.get("id"),
        "threadId": result.get("threadId"),
        "status": result.get("status"),
        "taskUrl": result.get("taskUrl") or result.get("reviewUrl"),
        "gmailDraftId": result.get("gmailDraftId"),
        "gmailThreadId": result.get("gmailThreadId"),
    }


def tool_operations_scan_run(args: dict[str, Any]) -> dict[str, Any]:
    payload = args.get("payload")
    if not isinstance(payload, dict):
        raise ToolError("invalid_input", "payload must be an object.")
    config = load_config()
    data = operations_post(config, "/api/emails/scan-runs", payload)
    if data.get("success") is False or data.get("ok") is False:
        raise ToolError("operations_error", "Operations Email Desk scan-run endpoint returned an error.", retryable=False)
    result = data.get("data") if isinstance(data.get("data"), dict) else data
    return {"ok": True, "scanRunId": result.get("scanRunId") or result.get("id"), "status": result.get("status")}


def tool_gmail_create_draft(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    if not gmail_drafts_enabled(config):
        return {"ok": False, "error": {"code": "gmail_drafts_disabled", "message": "Gmail draft creation is disabled until compose scope is approved.", "retryable": False}}
    thread_id = validate_safe_id("threadId", args.get("threadId"))
    to_email = validate_text("to", args.get("to"), required=True, max_len=240)
    subject = validate_text("subject", args.get("subject"), required=True, max_len=300)
    body = validate_text("body", args.get("body"), required=True, max_len=50000)
    service = google_build_service(config, ["https://www.googleapis.com/auth/gmail.compose"])
    msg = EmailMessage()
    msg["To"] = to_email
    msg["From"] = gmail_account(config)
    msg["Subject"] = subject
    if args.get("inReplyTo"):
        msg["In-Reply-To"] = str(args.get("inReplyTo"))
        msg["References"] = str(args.get("inReplyTo"))
    msg.set_content(body)
    raw = b64url(msg.as_bytes())
    draft = service.users().drafts().create(userId="me", body={"message": {"raw": raw, "threadId": thread_id}}).execute()
    return {"ok": True, "gmailDraftId": draft.get("id"), "threadId": thread_id}


def tool_email_send_telegram_message(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    chat_id = str(args.get("chat_id") or email_notify_chat_id(config))
    text = validate_text("text", args.get("text"), required=True, max_len=3900)
    thread_id = args.get("message_thread_id") or email_notify_thread_id(config)
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if thread_id:
        payload["message_thread_id"] = str(thread_id)
    url = f"https://api.telegram.org/bot{telegram_token(config)}/sendMessage"
    response = http_json(url, payload, {}, timeout=20)
    if not response.get("ok"):
        raise ToolError("telegram_error", "Telegram sendMessage failed.", retryable=True)
    result = response.get("result") or {}
    return {"ok": True, "message_id": result.get("message_id"), "message_thread_id": result.get("message_thread_id")}


def tool_email_memory_log(args: dict[str, Any]) -> dict[str, Any]:
    content = validate_text("content", args.get("content"), required=True, max_len=2000)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = MEMORY_DIR / f"{dt.date.today().isoformat()}.md"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"- {now_iso()} {content.strip()}\n")
    return {"ok": True}


TOOLS: dict[str, tuple[str, dict[str, Any], Callable[[dict[str, Any]], dict[str, Any]]]] = {
    "owlswatch_email_search_recent_threads": ("Search recent Owl's Watch Gmail threads read-only.", {"type": "object", "properties": {"hours": {"type": "integer", "minimum": 1, "maximum": 168}, "query": {"type": ["string", "null"]}, "maxResults": {"type": "integer", "minimum": 1, "maximum": 25}, "includeHandled": {"type": "boolean"}}, "additionalProperties": False}, tool_gmail_search_recent_threads),
    "owlswatch_email_search_unanswered_threads": ("Find recent Gmail threads whose latest meaningful message appears external/unanswered.", {"type": "object", "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 30}, "query": {"type": ["string", "null"]}, "maxResults": {"type": "integer", "minimum": 1, "maximum": 50}}, "additionalProperties": False}, tool_gmail_search_unanswered_threads),
    "owlswatch_email_read_thread": ("Read one Owl's Watch Gmail thread read-only.", {"type": "object", "properties": {"threadId": {"type": "string"}}, "required": ["threadId"], "additionalProperties": False}, tool_gmail_read_thread),
    "owlswatch_email_resolve_gmail_url": ("Resolve a Gmail web URL to a readable Gmail thread when Gmail exposes a compatible API id.", {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"], "additionalProperties": False}, tool_gmail_resolve_url),
    "owlswatch_luna_get_email_response_context": ("Fetch approved guest-shareable Luna context for an email response.", {"type": "object", "properties": {"clientQuestion": {"type": "string"}, "language": {"type": ["string", "null"]}, "topics": {"type": "array", "items": {"type": "string"}}, "factLimit": {"type": "integer"}, "blockLimit": {"type": "integer"}, "mediaLimit": {"type": "integer"}}, "required": ["clientQuestion"], "additionalProperties": False}, tool_luna_get_email_response_context),
    "owlswatch_email_upsert_task": ("Create or update a durable local email draft/review task.", {"type": "object", "properties": {"task": {"type": "object", "additionalProperties": True}}, "required": ["task"], "additionalProperties": False}, tool_email_upsert_task),
    "owlswatch_email_list_open_tasks": ("List durable local email tasks needing review or summary.", {"type": "object", "properties": {"statuses": {"type": "array", "items": {"type": "string"}}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "additionalProperties": False}, tool_email_list_open_tasks),
    "owlswatch_email_submit_operations_intake": ("Submit an email draft task to Operations Email Desk. Requires EMAIL_AGENT_API_TOKEN. Does not send email.", {"type": "object", "properties": {"payload": {"type": "object", "additionalProperties": True}}, "required": ["payload"], "additionalProperties": False}, tool_operations_email_intake),
    "owlswatch_email_submit_scan_run": ("Submit a daily/recent/unanswered email scan summary to Operations Email Desk. Requires EMAIL_AGENT_API_TOKEN.", {"type": "object", "properties": {"payload": {"type": "object", "additionalProperties": True}}, "required": ["payload"], "additionalProperties": False}, tool_operations_scan_run),
    "owlswatch_email_create_gmail_draft": ("Create a Gmail draft in the original thread when compose scope is explicitly enabled. Never sends.", {"type": "object", "properties": {"threadId": {"type": "string"}, "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}, "inReplyTo": {"type": ["string", "null"]}}, "required": ["threadId", "to", "subject", "body"], "additionalProperties": False}, tool_gmail_create_draft),
    "owlswatch_email_send_telegram_message": ("Send an email-agent Telegram notification to the configured Owl's Watch ops chat/topic.", {"type": "object", "properties": {"text": {"type": "string"}, "chat_id": {"type": ["string", "number", "null"]}, "message_thread_id": {"type": ["string", "number", "null"]}}, "required": ["text"], "additionalProperties": False}, tool_email_send_telegram_message),
    "owlswatch_email_memory_log": ("Append one concise Correo memory line.", {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"], "additionalProperties": False}, tool_email_memory_log),
}


def rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        return rpc_result(request_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "owlswatch_email", "version": "0.1.0"}})
    if method == "tools/list":
        return rpc_result(request_id, {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (d, s, _) in TOOLS.items()]})
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
