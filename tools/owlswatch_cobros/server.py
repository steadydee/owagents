#!/usr/bin/env python3
"""Narrow tools for Owl's Watch cuentas de cobro.

Cobros is a drafting clerk. This server keeps Gmail, Drive, Gmail-draft,
Operations Email Desk, Telegram, and local memory side effects behind
structured tools so the model never receives raw credentials.
"""

from __future__ import annotations

import base64
import datetime as dt
import email.utils
import hashlib
import html
import io
import json
import mimetypes
import os
import re
import sys
import traceback
import urllib.parse
import urllib.request
import zipfile
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable

WORKSPACE = Path(os.environ.get("OWLSWATCH_COBROS_WORKSPACE", "~/.openclaw/workspace-owlswatch-cobros")).expanduser()
CONFIG_PATH = Path(os.environ.get("OPENCLAW_CONFIG_PATH", "~/.openclaw-owlswatch/openclaw.json")).expanduser()
REPO_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "cobros" / "operator-billing-profiles.yaml"
DEFAULT_LOGO_PATHS = (
    Path(__file__).resolve().parent / "assets" / "WATERMARK FULL LOGO.png",
    Path(__file__).resolve().parents[2] / "openclaw" / "agents" / "cotiza" / "assets" / "WATERMARK FULL LOGO.png",
    Path("/Users/agent/Downloads/WATERMARK FULL LOGO.png"),
)
SPOOL_DIR = WORKSPACE / "spool" / "cobros"
MEMORY_DIR = WORKSPACE / "memory"

DEFAULT_GMAIL_ACCOUNT = "info@owlswatch.com"
DEFAULT_OPERATIONS_BASE_URL = "https://operations.owlswatch.com"
DEFAULT_COBROS_FOLDER_ID = "1xBqTQi7_QxTW-WRvmyAOnqafZXizK9wS"
DEFAULT_TEMPLATE_DOC_ID = "1cO6hgB-0ryRRWOvfuxg9jv8x7EIkTAOIDELTkGK0keE"
DEFAULT_NOTIFY_CHAT_ID = "-1003949383737"
REQUIRED_TEMPLATE_PLACEHOLDERS = (
    "{{DEBTOR_LEGAL_NAME}}",
    "{{DEBTOR_NIT}}",
    "{{AMOUNT_COP}}",
    "{{CONCEPT}}",
    "{{SERVICE_DATES}}",
    "{{CLIENT_REFERENCE}}",
    "{{PAYEE_NAME}}",
    "{{PAYEE_NIT}}",
    "{{PAYEE_CEDULA}}",
    "{{AMOUNT_WORDS_ES}}",
    "{{PAYEE_BANK}}",
    "{{PAYEE_ACCOUNT_TYPE}}",
    "{{PAYEE_ACCOUNT_NUMBER}}",
)

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/+\\-]{1,360}$")
TEXT_RE = re.compile(r"^[\s\S]{0,80000}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ACCOUNTING_TERMS = (
    "cuenta de cobro",
    "cuentas de cobro",
    "factura electronica",
    "factura electrónica",
    "rut",
    "nit",
    "dian",
    "por valor de",
)
DISPUTE_TERMS = (
    "no coincide",
    "diferencia",
    "diferencias",
    "correccion",
    "corrección",
    "corregir",
    "mismatch",
    "wrong amount",
    "difference",
    "not match",
)
REISSUE_TERMS = ("reexpedir", "reissue", "corregida", "corregido", "correction", "nueva cuenta", "replacement")

PAYEES_FALLBACK: dict[str, dict[str, Any]] = {
    "luz": {
        "key": "luz",
        "displayName": "Luz Adriana Valencia Ortiz",
        "cedulaNit": "30338298-9",
        "bankName": "Bancolombia",
        "accountType": "Ahorros",
        "accountNumber": "62352655835",
    },
    "dennis": {
        "key": "dennis",
        "displayName": "Dennis Thornton Bailey",
        "cedulaNit": "700179253-5",
        "bankName": "Bancolombia",
        "accountType": "Ahorros",
        "accountNumber": "05900011624",
    },
}
OPERATORS_FALLBACK: dict[str, dict[str, Any]] = {
    "colombia57": {
        "key": "colombia57",
        "legalName": "Colombia57 Tours",
        "nit": "090026196-8",
        "defaultPayee": "luz",
        "aliases": ["Colombia57", "Colombia 57", "Colombia57 Tours", "Promotora Neptuno"],
    },
    "jaguarundi": {
        "key": "jaguarundi",
        "legalName": "JAGUARUNDI TRAVEL SAS",
        "nit": "901687140-1",
        "defaultPayee": "luz",
        "aliases": ["Jaguarundi", "Jaguarundi Travel", "JAGUARUNDI TRAVEL SAS"],
    },
    "nature_experience": {
        "key": "nature_experience",
        "legalName": "NATURE EXPERIENCE SAS",
        "nit": "901937987-4",
        "defaultPayee": "dennis",
        "aliases": ["Nature Experience", "NATURE EXPERIENCE SAS"],
    },
    "manakin": {
        "key": "manakin",
        "legalName": "MANAKIN NATURE TOURS SAS",
        "nit": "900428891-9",
        "defaultPayee": "dennis",
        "aliases": ["Manakin", "Manakin Nature Tours", "MANAKIN NATURE TOURS SAS"],
    },
    "hacienda_venecia": {
        "key": "hacienda_venecia",
        "legalName": "Hacienda Venecia",
        "nit": "900310671-7",
        "defaultPayee": "luz",
        "aliases": ["Hacienda Venecia"],
    },
    "wild_about_colombia": {
        "key": "wild_about_colombia",
        "legalName": "Wild About Colombia",
        "nit": "901132193-8",
        "defaultPayee": "dennis",
        "aliases": ["Wild About Colombia"],
    },
    "de_una": {
        "key": "de_una",
        "legalName": "De Una Viajes",
        "nit": "830.146.707",
        "defaultPayee": None,
        "aliases": ["De Una", "De Una Viajes"],
    },
}

MONTHS: dict[str, tuple[int, str]] = {
    "enero": (1, "Jan"), "january": (1, "Jan"), "jan": (1, "Jan"),
    "febrero": (2, "Feb"), "february": (2, "Feb"), "feb": (2, "Feb"),
    "marzo": (3, "Mar"), "march": (3, "Mar"), "mar": (3, "Mar"),
    "abril": (4, "Apr"), "april": (4, "Apr"), "apr": (4, "Apr"),
    "mayo": (5, "May"), "may": (5, "May"),
    "junio": (6, "Jun"), "june": (6, "Jun"), "jun": (6, "Jun"),
    "julio": (7, "Jul"), "july": (7, "Jul"), "jul": (7, "Jul"),
    "agosto": (8, "Aug"), "august": (8, "Aug"), "aug": (8, "Aug"),
    "septiembre": (9, "Sep"), "setiembre": (9, "Sep"), "september": (9, "Sep"), "sep": (9, "Sep"),
    "octubre": (10, "Oct"), "october": (10, "Oct"), "oct": (10, "Oct"),
    "noviembre": (11, "Nov"), "november": (11, "Nov"), "nov": (11, "Nov"),
    "diciembre": (12, "Dec"), "december": (12, "Dec"), "dec": (12, "Dec"),
}


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
    mcp_env = config.get("mcp", {}).get("servers", {}).get("owlswatch_cobros", {}).get("env", {})
    value = mcp_env.get(key)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    env_vars = config.get("env", {}).get("vars", {})
    value = env_vars.get(key)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    cobros_cfg = config.get("cobros", {})
    camel = key.lower().split("_")
    candidate = camel[0] + "".join(part.title() for part in camel[1:])
    value = cobros_cfg.get(candidate)
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    return None


def validate_text(name: str, value: Any, required: bool = False, max_len: int = 80000) -> str | None:
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


def normalize_spaces(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", html.unescape(value)).strip(" \t\r\n-:;,.")
    return text or None


def remove_accents(value: str) -> str:
    table = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return value.translate(table)


def comparable(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", remove_accents(value).lower()).strip()


def profile_data() -> dict[str, Any]:
    try:
        import yaml  # type: ignore
        path = Path(os.environ.get("OWLSWATCH_COBROS_PROFILES_PATH", REPO_DATA_PATH)).expanduser()
        if path.is_file():
            data = yaml.safe_load(path.read_text()) or {}
            payees = data.get("payees") or PAYEES_FALLBACK
            operators = data.get("operators") or OPERATORS_FALLBACK
            for key, value in payees.items():
                value.setdefault("key", key)
            for key, value in operators.items():
                value.setdefault("key", key)
            return {"payees": payees, "operators": operators}
    except Exception:
        pass
    return {"payees": PAYEES_FALLBACK, "operators": OPERATORS_FALLBACK}


def gmail_account(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "OWLSWATCH_GMAIL_ACCOUNT") or config.get("gmail", {}).get("account") or DEFAULT_GMAIL_ACCOUNT
    if not isinstance(raw, str) or not EMAIL_RE.match(raw):
        raise ToolError("config_invalid", "Configured Gmail account is malformed.")
    return raw


def google_workspace_impersonation_user(config: dict[str, Any]) -> str | None:
    raw = cfg_env(config, "GOOGLE_WORKSPACE_IMPERSONATE_USER") or cfg_env(config, "GOOGLE_DRIVE_IMPERSONATE_USER")
    if raw is None:
        return None
    if not EMAIL_RE.match(raw):
        raise ToolError("config_invalid", "Configured Google Workspace impersonation user is malformed.")
    return raw


def google_credentials_path(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "GOOGLE_APPLICATION_CREDENTIALS")
    if not raw:
        raw = config.get("googleDrive", {}).get("serviceAccountCredentials")
    if not isinstance(raw, str) or not raw or raw.startswith("<"):
        raise ToolError("config_missing", "Google service account credentials are missing from tool environment/config.")
    path = Path(raw).expanduser()
    if not path.is_file():
        raise ToolError("config_missing", "Google service account credentials file was not found.")
    return str(path)


def google_build_service(config: dict[str, Any], api: str, version: str, scopes: list[str], delegated_subject: str | None = None) -> Any:
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ToolError("dependency_missing", "Google API Python packages are not installed in the Cobros tool runtime.") from exc
    credentials = service_account.Credentials.from_service_account_file(google_credentials_path(config), scopes=scopes)
    if delegated_subject:
        credentials = credentials.with_subject(delegated_subject)
    return build(api, version, credentials=credentials)


def operations_base_url(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "OPERATIONS_BASE_URL") or cfg_env(config, "OPERATIONS_API_BASE_URL") or DEFAULT_OPERATIONS_BASE_URL
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ToolError("config_invalid", "Operations base URL must be an https URL.")
    return raw.rstrip("/")


def read_secret_file(path_text: str | None) -> str | None:
    if not path_text:
        return None
    path = Path(path_text).expanduser()
    if not path.is_file():
        return None
    value = path.read_text().strip()
    return value or None


def operations_email_token(config: dict[str, Any]) -> str | None:
    return cfg_env(config, "EMAIL_AGENT_API_TOKEN") or read_secret_file(cfg_env(config, "EMAIL_AGENT_API_TOKEN_FILE"))


def cobros_folder_id(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "OWLSWATCH_COBROS_FOLDER_ID") or DEFAULT_COBROS_FOLDER_ID
    return validate_safe_id("OWLSWATCH_COBROS_FOLDER_ID", raw) or DEFAULT_COBROS_FOLDER_ID


def cobros_template_doc_id(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "OWLSWATCH_COBROS_TEMPLATE_DOC_ID") or DEFAULT_TEMPLATE_DOC_ID
    return validate_safe_id("OWLSWATCH_COBROS_TEMPLATE_DOC_ID", raw) or DEFAULT_TEMPLATE_DOC_ID


def telegram_token(config: dict[str, Any]) -> str:
    raw = cfg_env(config, "TELEGRAM_BOT_TOKEN") or config.get("channels", {}).get("telegram", {}).get("botToken")
    if not isinstance(raw, str) or not raw or raw.startswith("<"):
        raise ToolError("config_missing", "Telegram bot token is missing from tool environment/config.")
    return raw


def cobros_notify_chat_id(config: dict[str, Any]) -> str:
    return cfg_env(config, "OWLSWATCH_COBROS_NOTIFY_CHAT_ID") or DEFAULT_NOTIFY_CHAT_ID


def cobros_notify_thread_id(config: dict[str, Any]) -> str | None:
    return cfg_env(config, "OWLSWATCH_COBROS_NOTIFY_THREAD_ID")


def http_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def decode_gmail_part(part: dict[str, Any]) -> str:
    data = part.get("body", {}).get("data")
    if not data:
        return ""
    padded = data + ("=" * ((4 - len(data) % 4) % 4))
    try:
        return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")
    except Exception:
        return ""


def extract_message_body(payload: dict[str, Any]) -> str:
    plain: list[str] = []
    html_parts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            plain.append(decode_gmail_part(part))
        elif mime == "text/html":
            html_parts.append(decode_gmail_part(part))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    if plain:
        return "\n".join(x for x in plain if x)
    if html_parts:
        text = re.sub(r"<br\s*/?>", "\n", "\n".join(html_parts), flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        return html.unescape(re.sub(r"\s+", " ", text)).strip()
    return ""


def header_value(headers: list[dict[str, str]], name: str) -> str | None:
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value")
    return None


def parse_email(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    name, addr = email.utils.parseaddr(value)
    return normalize_spaces(name), addr.lower() if addr else None


def tool_search_gmail_threads(args: dict[str, Any]) -> dict[str, Any]:
    query = validate_text("query", args.get("query"), required=False, max_len=500) or ""
    max_results = int(args.get("maxResults") or 10)
    if not 1 <= max_results <= 20:
        raise ToolError("invalid_input", "maxResults must be between 1 and 20.")
    terms = " OR ".join([f'"{term}"' for term in ("cuenta de cobro", "factura electronica", "factura electrónica", "RUT", "DIAN")])
    gmail_query = f"({terms}) -in:spam -in:trash"
    if query.strip():
        gmail_query = f"{gmail_query} {query.strip()}"
    config = load_config()
    service = google_build_service(config, "gmail", "v1", ["https://www.googleapis.com/auth/gmail.readonly"], gmail_account(config))
    response = service.users().messages().list(userId="me", q=gmail_query, maxResults=max_results).execute()
    matches = []
    for item in response.get("messages") or []:
        msg = service.users().messages().get(userId="me", id=item["id"], format="metadata", metadataHeaders=["Subject", "From", "Date"]).execute()
        headers = msg.get("payload", {}).get("headers") or []
        matches.append({
            "threadId": msg.get("threadId"),
            "messageId": msg.get("id"),
            "subject": header_value(headers, "Subject"),
            "from": header_value(headers, "From"),
            "date": header_value(headers, "Date"),
            "snippet": msg.get("snippet"),
        })
    return {"ok": True, "query": gmail_query, "matches": matches}


def tool_read_gmail_thread(args: dict[str, Any]) -> dict[str, Any]:
    thread_id = validate_safe_id("threadId", args.get("threadId"))
    config = load_config()
    service = google_build_service(config, "gmail", "v1", ["https://www.googleapis.com/auth/gmail.readonly"], gmail_account(config))
    thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    messages = []
    for msg in thread.get("messages") or []:
        payload = msg.get("payload", {})
        headers = payload.get("headers") or []
        from_name, from_email = parse_email(header_value(headers, "From"))
        attachments = []
        stack = [payload]
        while stack:
            part = stack.pop()
            body = part.get("body") or {}
            filename = part.get("filename")
            if filename:
                attachments.append({"fileName": filename, "mimeType": part.get("mimeType"), "size": body.get("size")})
            stack.extend(part.get("parts") or [])
        messages.append({
            "gmailMessageId": msg.get("id"),
            "rfc822MessageId": header_value(headers, "Message-ID"),
            "threadId": msg.get("threadId"),
            "from": header_value(headers, "From"),
            "fromName": from_name,
            "fromEmail": from_email,
            "to": header_value(headers, "To"),
            "cc": header_value(headers, "Cc"),
            "date": header_value(headers, "Date"),
            "subject": header_value(headers, "Subject"),
            "snippet": msg.get("snippet"),
            "bodyText": extract_message_body(payload),
            "attachments": attachments,
        })
    raw_text = "\n\n".join(f"From: {m.get('from')}\nSubject: {m.get('subject')}\n\n{m.get('bodyText')}" for m in messages)
    return {
        "ok": True,
        "thread": {
            "threadId": thread_id,
            "sourceUrl": f"https://mail.google.com/mail/u/0/#inbox/{thread_id}",
            "messages": messages,
            "rawText": raw_text,
        },
    }


def parse_money(value: str) -> int | None:
    text = value.strip()
    if "," in text and len(text.rsplit(",", 1)[-1]) <= 2:
        text = text.rsplit(",", 1)[0]
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    amount = int(digits)
    if amount <= 0 or amount > 500_000_000:
        return None
    return amount


def extract_amounts(text: str) -> tuple[int | None, list[int]]:
    money_pattern = r"(?:COP\s*)?\$?\s*\d{1,3}(?:[.,]\d{3})+(?:,\d{2})?|\b\d{6,9}\b"
    all_amounts = []
    for match in re.finditer(money_pattern, text, re.I):
        amount = parse_money(match.group(0))
        if amount and amount not in all_amounts:
            all_amounts.append(amount)
    main = None
    for pattern in (
        r"por\s+valor\s+de\s*(" + money_pattern + r")",
        r"valor\s+de\s*(" + money_pattern + r")",
        r"total\s*[:\-]?\s*(" + money_pattern + r")",
        r"monto\s*[:\-]?\s*(" + money_pattern + r")",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            main = parse_money(match.group(1))
            break
    if main is None and len(all_amounts) == 1:
        main = all_amounts[0]
    return main, all_amounts


def amount_words_es(amount: int) -> str:
    units = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
    teens = {
        10: "diez", 11: "once", 12: "doce", 13: "trece", 14: "catorce", 15: "quince",
        16: "dieciseis", 17: "diecisiete", 18: "dieciocho", 19: "diecinueve",
    }
    tens = ["", "", "veinte", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
    hundreds = {
        1: "ciento", 2: "doscientos", 3: "trescientos", 4: "cuatrocientos", 5: "quinientos",
        6: "seiscientos", 7: "setecientos", 8: "ochocientos", 9: "novecientos",
    }

    def under_100(n: int) -> str:
        if n < 10:
            return units[n]
        if n < 20:
            return teens[n]
        if n == 20:
            return "veinte"
        if n < 30:
            return "veinti" + units[n - 20]
        d, u = divmod(n, 10)
        return tens[d] if u == 0 else f"{tens[d]} y {units[u]}"

    def under_1000(n: int) -> str:
        if n == 0:
            return ""
        if n == 100:
            return "cien"
        if n < 100:
            return under_100(n)
        h, r = divmod(n, 100)
        return hundreds[h] if r == 0 else f"{hundreds[h]} {under_100(r)}"

    def chunk(n: int) -> str:
        if n < 1000:
            return under_1000(n)
        th, r = divmod(n, 1000)
        prefix = "mil" if th == 1 else f"{under_1000(th)} mil"
        return prefix if r == 0 else f"{prefix} {under_1000(r)}"

    if amount == 0:
        words = "cero"
    else:
        millions, remainder = divmod(amount, 1_000_000)
        parts = []
        if millions:
            parts.append("un millon" if millions == 1 else f"{chunk(millions)} millones")
        if remainder:
            parts.append(chunk(remainder))
        words = " ".join(parts)
    return f"{words[:1].upper()}{words[1:]} pesos colombianos"


def extract_service_dates(text: str) -> str | None:
    clean = remove_accents(text)
    month_keys = "|".join(sorted((re.escape(k) for k in MONTHS), key=len, reverse=True))
    range_re = re.compile(rf"\b(\d{{1,2}})\s*(?:al|a|-|/)\s*(\d{{1,2}})\s*(?:de\s*)?({month_keys})\s*(?:de\s*)?(\d{{4}})", re.I)
    match = range_re.search(clean)
    if match:
        start, end, month, year = match.groups()
        return f"{MONTHS[month.lower()][1]} {int(start)}-{int(end)} {year}"
    single_re = re.compile(rf"\b(\d{{1,2}})\s*(?:de\s*)?({month_keys})\s*(?:de\s*)?(\d{{4}})", re.I)
    match = single_re.search(clean)
    if match:
        day, month, year = match.groups()
        return f"{MONTHS[month.lower()][1]} {int(day)} {year}"
    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if iso:
        year, month, day = iso.groups()
        month_name = dt.date(int(year), int(month), int(day)).strftime("%b")
        return f"{month_name} {int(day)} {year}"
    return None


def find_operator(text: str, profiles: dict[str, Any]) -> dict[str, Any] | None:
    haystack = comparable(text)
    for key, profile in profiles["operators"].items():
        aliases = [profile.get("legalName") or "", *(profile.get("aliases") or [])]
        for alias in aliases:
            if alias and comparable(alias) in haystack:
                return {"key": key, **profile}
    return None


def infer_unknown_operator(text: str) -> dict[str, Any] | None:
    nit_match = re.search(r"\bNIT\s*[:#\-]?\s*([0-9][0-9.\- ]{5,20}[0-9])", text, re.I)
    name_match = re.search(r"(?:operador|raz[oó]n social|empresa|agencia)\s*[:\-]\s*([^\n]{3,90})", text, re.I)
    if nit_match and name_match:
        return {
            "key": "unknown",
            "legalName": normalize_spaces(name_match.group(1)),
            "nit": normalize_spaces(nit_match.group(1)),
            "defaultPayee": None,
            "aliases": [],
        }
    return None


def find_payee(text: str, operator: dict[str, Any] | None, profiles: dict[str, Any]) -> dict[str, Any] | None:
    haystack = comparable(text)
    for key, payee in profiles["payees"].items():
        if comparable(payee.get("displayName", "")) in haystack:
            return {"key": key, **payee}
    default_key = operator.get("defaultPayee") if operator else None
    if default_key and default_key in profiles["payees"]:
        return {"key": default_key, **profiles["payees"][default_key]}
    return None


def extract_client_reference(text: str) -> str | None:
    patterns = [
        r"REFERENCIA\s*-\s*([^\n]{2,90})",
        r"REFERENCIA\s+DEL\s+CLIENTE\s*:\s*([\s\S]{2,180}?)(?:\n\s*(?:NOMBRE\s+PROVEEDOR|TIPO\s+DE\s+SERVICIO|FECHA\s+DE\s+SERVICIO|SOPORTES|$))",
        r"\bcliente\s+([\s\S]{2,120}?)\s*/\s*([A-Z0-9][A-Z0-9\-]{3,40})",
        r"\bcliente\s*[:\-]\s*([^\n]{2,90})",
        r"\breferencia\s*[:\-]\s*([^\n]{2,90})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        if len(match.groups()) >= 2:
            value = f"{match.group(1)} / {match.group(2)}"
        else:
            value = match.group(1)
        lines = [normalize_spaces(line) for line in value.splitlines()]
        lines = [line for line in lines if line]
        if lines:
            return clean_client_reference(" / ".join(lines[:3]))
    return None


def clean_client_reference(value: str) -> str:
    value = re.sub(r"[*_`]+", " ", value)
    parts = []
    for part in value.split("/"):
        part = re.sub(r"^[\\W_]+|[\\W_]+$", " ", part)
        cleaned = normalize_spaces(part)
        if cleaned:
            parts.append(cleaned)
    return " / ".join(parts[:3])


def extract_concept(text: str) -> str | None:
    match = re.search(r"TIPO\s+DE\s+SERVICIO\s*:\s*([^\n]{3,120})", text, re.I)
    source = match.group(1) if match else text
    comp = comparable(source)
    if "alojamiento" in comp or "hospedaje" in comp or "hotel" in comp:
        return "Hospedaje"
    if "tour" in comp or "aves" in comp or "bird" in comp:
        return "Servicios de turismo / avistamiento de aves"
    if "transporte" in comp or "transport" in comp:
        return "Transporte"
    if match:
        return normalize_spaces(match.group(1))
    return None


def source_text_from_args(args: dict[str, Any]) -> str:
    raw = validate_text("raw_text", args.get("raw_text"), required=False)
    if raw:
        return raw
    thread = args.get("thread")
    if isinstance(thread, dict):
        if isinstance(thread.get("rawText"), str):
            return thread["rawText"]
        messages = thread.get("messages")
        if isinstance(messages, list):
            return "\n\n".join(str(m.get("bodyText") or m.get("snippet") or "") for m in messages if isinstance(m, dict))
    raise ToolError("invalid_input", "raw_text or thread.rawText is required.")


def sent_cuenta_pdf_exists(text: str, thread: dict[str, Any] | None = None) -> bool:
    if re.search(r"\b(cuenta.+\.pdf|pdf.+cuenta|adjunt[oa].+cuenta de cobro)\b", comparable(text)):
        return True
    if thread and isinstance(thread.get("messages"), list):
        for msg in thread["messages"]:
            for attachment in msg.get("attachments") or []:
                filename = comparable(str(attachment.get("fileName") or ""))
                if "cuenta" in filename and filename.endswith("pdf"):
                    return True
    return False


def build_review_question(missing: list[str]) -> str:
    prompts = {
        "operator_legal_name_and_nit": "What is the operator legal name and NIT?",
        "amount": "What is the exact COP amount for the cuenta de cobro?",
        "service_dates": "What service date or date range should appear on the cuenta de cobro?",
        "concept": "What service concept should appear: hospedaje, tour, transporte, or another concept?",
        "payee": "Should this cuenta de cobro be under Luz Adriana Valencia Ortiz or Dennis Thornton Bailey?",
    }
    return prompts.get(missing[0], "What missing detail should I use for the cuenta de cobro?")


def override_text(value: Any, max_len: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_len]


def override_amount_cop(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        amount = value
    else:
        amount = parse_money(str(value))
    if amount is None or amount <= 0 or amount > 500_000_000:
        raise ToolError("invalid_input", "override_fields.amountCop must be a valid COP amount.")
    return amount


def tool_prepare(args: dict[str, Any]) -> dict[str, Any]:
    text = source_text_from_args(args)
    source_metadata = args.get("source_metadata") if isinstance(args.get("source_metadata"), dict) else {}
    thread = args.get("thread") if isinstance(args.get("thread"), dict) else None
    override_fields = args.get("override_fields") if isinstance(args.get("override_fields"), dict) else {}
    human_override = bool(args.get("human_override")) or bool(override_fields)
    profiles = profile_data()
    operator = find_operator(text, profiles) or infer_unknown_operator(text)
    payee = find_payee(text, operator, profiles)
    amount, all_amounts = extract_amounts(text)
    service_dates = extract_service_dates(text)
    concept = extract_concept(text)
    client_reference = extract_client_reference(text)

    if override_fields:
        if override_fields.get("operatorKey") in profiles.get("operators", {}):
            operator = profiles["operators"][override_fields["operatorKey"]]
        if override_fields.get("payeeKey") in profiles.get("payees", {}):
            payee = profiles["payees"][override_fields["payeeKey"]]
        amount = override_amount_cop(override_fields.get("amountCop")) or amount
        service_dates = override_text(override_fields.get("serviceDates")) or service_dates
        concept = override_text(override_fields.get("concept")) or concept
        client_reference = override_text(override_fields.get("clientReference")) or client_reference

    warnings = []
    comp = comparable(text)
    has_dispute_terms = any(term in comp for term in (comparable(t) for t in DISPUTE_TERMS))
    if has_dispute_terms and not human_override:
        return {
            "ok": True,
            "status": "needs_human",
            "reason": "amount_dispute_or_correction_mentioned",
            "warnings": ["amount_dispute_or_correction_mentioned"],
            "fields": {
                "operatorKey": operator.get("key") if operator else None,
                "debtorLegalName": operator.get("legalName") if operator else None,
                "debtorNit": operator.get("nit") if operator else None,
                "amountCop": amount,
                "allAmountsCop": all_amounts,
                "serviceDates": service_dates,
                "clientReference": client_reference,
                "concept": concept,
            },
        }
    if has_dispute_terms and human_override:
        warnings.append("human_override_after_amount_dispute_or_correction")
    if "rut" in comp or "factura electronica" in comp or "factura electrónica" in text.lower():
        warnings.append("rut_or_electronic_invoice_requested")
    if len(all_amounts) > 1:
        warnings.append("multiple_amounts_found")
    if sent_cuenta_pdf_exists(text, thread) and not any(term in comp for term in (comparable(t) for t in REISSUE_TERMS)):
        return {
            "ok": True,
            "status": "duplicate",
            "reason": "thread_already_appears_to_have_sent_cuenta_pdf",
            "warnings": ["possible_duplicate_sent_pdf"],
            "fields": {"clientReference": client_reference, "amountCop": amount, "serviceDates": service_dates},
        }

    missing = []
    if not operator or not operator.get("legalName") or not operator.get("nit"):
        missing.append("operator_legal_name_and_nit")
    if amount is None:
        missing.append("amount")
    if not service_dates:
        missing.append("service_dates")
    if not concept:
        missing.append("concept")
    if not payee:
        missing.append("payee")

    fields = {
        "operatorKey": operator.get("key") if operator else None,
        "debtorLegalName": operator.get("legalName") if operator else None,
        "debtorNit": operator.get("nit") if operator else None,
        "payeeKey": payee.get("key") if payee else None,
        "payee": payee,
        "amountCop": amount,
        "amountWordsEs": amount_words_es(amount) if amount is not None else None,
        "allAmountsCop": all_amounts,
        "serviceDates": service_dates,
        "clientReference": client_reference,
        "concept": concept,
    }
    if missing:
        return {
            "ok": True,
            "status": "needs_info",
            "missingFields": missing,
            "question": build_review_question(missing),
            "warnings": warnings,
            "fields": fields,
            "sourceMetadata": source_metadata,
        }
    return {
        "ok": True,
        "status": "ready",
        "warnings": warnings,
        "fields": fields,
        "sourceMetadata": source_metadata,
    }


def safe_filename(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9 ._+&'()-]+", "", value).strip()
    return text[:150] or "Cuenta de Cobro"


def packet_title(fields: dict[str, Any]) -> str:
    operator = fields.get("debtorLegalName") or "Operador"
    client = fields.get("clientReference") or "Cliente"
    dates = fields.get("serviceDates") or "Fecha"
    return safe_filename(f"Cuenta de Cobro - {operator} - {client} - {dates}")


def template_replacements(fields: dict[str, Any]) -> dict[str, str]:
    payee = fields.get("payee") or {}
    amount = int(fields["amountCop"])
    payee_nit = payee.get("cedulaNit") or ""
    replacements = {
        "{{DEBTOR_LEGAL_NAME}}": fields.get("debtorLegalName") or "",
        "{{DEBTOR_NIT}}": fields.get("debtorNit") or "",
        "{{CLIENT_REFERENCE}}": fields.get("clientReference") or "",
        "{{SERVICE_DATES}}": fields.get("serviceDates") or "",
        "{{CONCEPT}}": fields.get("concept") or "",
        "{{AMOUNT_COP}}": f"${amount:,.0f}",
        "{{AMOUNT_WORDS_ES}}": fields.get("amountWordsEs") or amount_words_es(amount),
        "{{PAYEE_NAME}}": payee.get("displayName") or "",
        "{{PAYEE_NIT}}": payee_nit,
        "{{PAYEE_CEDULA}}": cedula_without_check_digit(payee_nit),
        "{{PAYEE_BANK}}": payee.get("bankName") or "",
        "{{PAYEE_ACCOUNT_TYPE}}": payee.get("accountType") or "",
        "{{PAYEE_ACCOUNT_NUMBER}}": payee.get("accountNumber") or "",
        "{{TODAY}}": dt.date.today().isoformat(),
    }
    legacy = {
        "NOMBRE_OPERADOR": replacements["{{DEBTOR_LEGAL_NAME}}"],
        "NIT_OPERADOR": replacements["{{DEBTOR_NIT}}"],
        "REFERENCIA_CLIENTE": replacements["{{CLIENT_REFERENCE}}"],
        "FECHA_SERVICIO": replacements["{{SERVICE_DATES}}"],
        "CONCEPTO_SERVICIO": replacements["{{CONCEPT}}"],
        "VALOR_COP": replacements["{{AMOUNT_COP}}"],
        "VALOR_LETRAS": replacements["{{AMOUNT_WORDS_ES}}"],
        "NOMBRE_PROVEEDOR": replacements["{{PAYEE_NAME}}"],
        "NIT_PROVEEDOR": replacements["{{PAYEE_NIT}}"],
    }
    replacements.update(legacy)
    return replacements


def format_cop(amount: int) -> str:
    return f"${amount:,.0f}"


def logo_data_uri() -> str:
    for path in DEFAULT_LOGO_PATHS:
        try:
            if path.is_file():
                return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
    return ""


def cedula_without_check_digit(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"-\d+$", "", value.strip())


def html_template_replacements(fields: dict[str, Any]) -> dict[str, str]:
    return {key: html.escape(value, quote=False) for key, value in template_replacements(fields).items()}


def cobros_document_html(fields: dict[str, Any]) -> str:
    payee = fields.get("payee") or {}
    amount = int(fields["amountCop"])
    logo = logo_data_uri()
    logo_img = (
        f'<img src="{logo}" alt="Owl&apos;s Watch" width="96" height="96" '
        'style="width:96px;height:96px;object-fit:contain;border:0;display:block;">'
        if logo else ""
    )
    payee_name = payee.get("displayName") or ""
    payee_nit = payee.get("cedulaNit") or ""
    payee_name_line = f"{payee_name} - Owl's Watch" if payee_name else "Owl's Watch"
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    @page {{ size: letter; margin: 0.65in 0.95in; }}
    body {{
      color: #111;
      font-family: "Century Gothic", "Avenir Next", Arial, Helvetica, sans-serif;
      font-size: 12pt;
      line-height: 1.25;
      margin: 0;
    }}
    h1, h2 {{
      font-family: "Century Gothic", "Avenir Next", Arial, Helvetica, sans-serif;
    }}
    h1 {{
      font-size: 14pt;
      font-weight: 700;
      letter-spacing: 0;
      margin: 0 0 10px;
      text-align: center;
    }}
    h2 {{
      font-size: 13pt;
      font-weight: 700;
      margin: 28px 0 7px;
    }}
    p {{
      margin: 0 0 8px;
    }}
    .reference {{
      font-size: 11pt;
      margin-top: 6px;
    }}
    .payment {{
      font-size: 11.5pt;
      margin-top: 82px;
      width: 3.2in;
    }}
    .payment p {{
      margin-bottom: 6px;
    }}
    .main {{
      margin: 26px auto 0;
      max-width: 5.6in;
      text-align: center;
    }}
  </style>
</head>
<body>
  <table border="0" cellpadding="0" cellspacing="0" style="width:100%; border-collapse:collapse; border:0;">
    <tr>
      <td style="width:1.25in; vertical-align:top; border:0;">{logo_img}</td>
      <td></td>
    </tr>
  </table>
  <div class="main" align="center">
      <h1>Cuenta de Cobro</h1>
      <p align="center">{html.escape(fields.get("debtorLegalName") or "")}</p>
      <p align="center">NIT: {html.escape(fields.get("debtorNit") or "")}</p>

      <h2>Debe a:</h2>
      <p align="center">{html.escape(payee_name_line)}</p>
      <p align="center">NIT: {html.escape(payee_nit)}</p>

      <h2>La Suma de:</h2>
      <p align="center">{html.escape(format_cop(amount))}</p>
      <p align="center">{html.escape(fields.get("amountWordsEs") or amount_words_es(amount))}</p>

      <h2>Por Concepto:</h2>
      <p align="center">{html.escape(fields.get("concept") or "")}</p>
      <p align="center">{html.escape(fields.get("serviceDates") or "")}</p>
      <p align="center" class="reference">Referencia: {html.escape(fields.get("clientReference") or "")}</p>
  </div>

    <section class="payment" align="left">
      <p>Por favor consignar:</p>
      <p>{html.escape(payee.get("accountType") or "Cuenta")} {html.escape(payee.get("bankName") or "")}</p>
      <p># {html.escape(payee.get("accountNumber") or "")}</p>
      <p>A nombre de:</p>
      <p>{html.escape(payee_name)}</p>
      <p>Cédula: {html.escape(cedula_without_check_digit(payee_nit))}</p>
    </section>
</body>
</html>"""


def cobros_placeholder_template_html() -> str:
    fields = {
        "debtorLegalName": "{{DEBTOR_LEGAL_NAME}}",
        "debtorNit": "{{DEBTOR_NIT}}",
        "clientReference": "{{CLIENT_REFERENCE}}",
        "serviceDates": "{{SERVICE_DATES}}",
        "concept": "{{CONCEPT}}",
        "amountCop": 123456789,
        "amountWordsEs": "{{AMOUNT_WORDS_ES}}",
        "payee": {
            "displayName": "{{PAYEE_NAME}}",
            "cedulaNit": "{{PAYEE_NIT}}",
            "bankName": "{{PAYEE_BANK}}",
            "accountType": "{{PAYEE_ACCOUNT_TYPE}}",
            "accountNumber": "{{PAYEE_ACCOUNT_NUMBER}}",
        },
    }
    return (
        cobros_document_html(fields)
        .replace("$123,456,789", "{{AMOUNT_COP}}")
        .replace("Cédula: {{PAYEE_NIT}}", "Cédula: {{PAYEE_CEDULA}}")
    )


def unpack_google_doc_html(raw: bytes) -> str:
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        return raw.decode("utf-8", errors="replace")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        html_names = [name for name in archive.namelist() if name.lower().endswith((".html", ".htm"))]
        if not html_names:
            raise ToolError("template_invalid", "Cobros template export did not include HTML.")
        html_name = html_names[0]
        text = archive.read(html_name).decode("utf-8", errors="replace")
        for name in archive.namelist():
            if name == html_name or name.endswith("/"):
                continue
            data = archive.read(name)
            mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            data_uri = "data:" + mime_type + ";base64," + base64.b64encode(data).decode("ascii")
            escaped = html.escape(name, quote=True)
            quoted = urllib.parse.quote(name)
            text = text.replace(name, data_uri).replace(escaped, data_uri).replace(quoted, data_uri)
        return text


def export_template_html(drive: Any, template_doc_id: str) -> str:
    raw = drive.files().export(fileId=template_doc_id, mimeType="text/html").execute()
    return unpack_google_doc_html(raw)


def render_template_html(template_html: str, fields: dict[str, Any]) -> str:
    missing = [placeholder for placeholder in REQUIRED_TEMPLATE_PLACEHOLDERS if placeholder not in template_html]
    if missing:
        raise ToolError(
            "template_invalid",
            "Cobros template is not variable-ready. It must contain the required {{...}} placeholders.",
        )
    rendered = template_html
    for placeholder, value in html_template_replacements(fields).items():
        rendered = rendered.replace(placeholder, value)
    return rendered


def render_document_html(config: dict[str, Any], drive: Any, fields: dict[str, Any]) -> tuple[str, str]:
    template_doc_id = cobros_template_doc_id(config)
    try:
        template_html = export_template_html(drive, template_doc_id)
        return render_template_html(template_html, fields), "google_doc_template"
    except ToolError as exc:
        if exc.code != "template_invalid":
            raise
    except Exception:
        pass
    return cobros_document_html(fields), "generated_fallback"


def create_doc_from_html_text(drive: Any, html_text: str, title: str, folder_id: str) -> dict[str, Any]:
    from googleapiclient.http import MediaIoBaseUpload
    html_bytes = html_text.encode("utf-8")
    media = MediaIoBaseUpload(io.BytesIO(html_bytes), mimetype="text/html", resumable=False)
    return drive.files().create(
        body={"name": title, "parents": [folder_id], "mimeType": "application/vnd.google-apps.document"},
        media_body=media,
        fields="id,webViewLink",
        supportsAllDrives=True,
    ).execute()


def create_doc_from_html(drive: Any, fields: dict[str, Any], title: str, folder_id: str) -> dict[str, Any]:
    return create_doc_from_html_text(drive, cobros_document_html(fields), title, folder_id)


def create_pdf_for_doc(drive: Any, doc_id: str, title: str, folder_id: str) -> tuple[bytes, dict[str, Any], str]:
    from googleapiclient.http import MediaIoBaseUpload
    pdf_bytes = drive.files().export(fileId=doc_id, mimeType="application/pdf").execute()
    pdf_name = f"{title}.pdf"
    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf", resumable=False)
    pdf_file = drive.files().create(
        body={"name": pdf_name, "parents": [folder_id], "mimeType": "application/pdf"},
        media_body=media,
        fields="id,webViewLink",
        supportsAllDrives=True,
    ).execute()
    return pdf_bytes, pdf_file, pdf_name


def should_fallback_to_drive_html(message: str) -> bool:
    lowered = message.lower()
    return (
        "docs.googleapis.com" in lowered
        or "google docs api" in lowered
        or "file not found" in lowered
        or "notfound" in lowered
        or "404" in lowered
    )


def create_doc_and_pdf(config: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    try:
        import io  # noqa: F401
        from googleapiclient.http import MediaIoBaseUpload  # noqa: F401
    except ImportError as exc:
        raise ToolError("dependency_missing", "Google API upload dependencies are not installed.") from exc
    delegated_user = google_workspace_impersonation_user(config)
    drive = google_build_service(config, "drive", "v3", ["https://www.googleapis.com/auth/drive"], delegated_user)
    title = packet_title(fields)
    folder_id = cobros_folder_id(config)
    try:
        html_text, template_source = render_document_html(config, drive, fields)
        copied = create_doc_from_html_text(drive, html_text, title, folder_id)
    except Exception as exc:
        message = str(exc)
        if "unauthorized_client" in message:
            raise ToolError("google_workspace_impersonation_unauthorized", "Google Workspace delegation is missing Drive scopes for Cobros document creation.") from exc
        if "storageQuotaExceeded" in message or "storage quota" in message.lower():
            raise ToolError("google_drive_storage_quota_exceeded", "Google Drive refused document creation because service-account-owned Drive storage has no quota. Configure Google Workspace impersonation for Drive writes.") from exc
        raise ToolError("google_drive_error", "Google Drive cuenta de cobro document creation failed.", retryable=True) from exc
    doc_id = copied["id"]
    try:
        pdf_bytes, pdf_file, pdf_name = create_pdf_for_doc(drive, doc_id, title, folder_id)
    except Exception as exc:
        raise ToolError("google_drive_error", "Google Drive cuenta de cobro PDF export failed.", retryable=True) from exc
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)
    pdf_local_path = SPOOL_DIR / f"{doc_id}.pdf"
    pdf_local_path.write_bytes(pdf_bytes)
    return {
        "title": title,
        "docFileId": doc_id,
        "docUrl": copied.get("webViewLink") or f"https://docs.google.com/document/d/{doc_id}/edit",
        "pdfFileId": pdf_file["id"],
        "pdfUrl": pdf_file.get("webViewLink") or f"https://drive.google.com/file/d/{pdf_file['id']}/view",
        "pdfFileName": pdf_name,
        "pdfLocalPath": str(pdf_local_path),
        "sizeBytes": len(pdf_bytes),
        "templateSource": template_source,
    }


def tool_create_packet(args: dict[str, Any]) -> dict[str, Any]:
    prepared = args.get("prepared") or args.get("prepared_cobro") or args.get("preparedCobro")
    if not isinstance(prepared, dict):
        raise ToolError("invalid_input", "prepared must be an object returned by owlswatch_cobros_prepare.")
    if prepared.get("status") != "ready":
        raise ToolError("not_ready", "Cobros packet can only be created from prepared status=ready.")
    fields = prepared.get("fields")
    if not isinstance(fields, dict):
        raise ToolError("invalid_input", "prepared.fields is required.")
    config = load_config()
    packet = create_doc_and_pdf(config, fields)
    return {"ok": True, "packet": packet, "warnings": prepared.get("warnings") or []}


def read_spooled_pdf(packet: dict[str, Any]) -> bytes:
    raw = packet.get("pdfLocalPath")
    if not isinstance(raw, str):
        raise ToolError("invalid_input", "packet.pdfLocalPath is required for Gmail draft attachment.")
    path = Path(raw).expanduser().resolve()
    workspace = WORKSPACE.resolve()
    if workspace not in path.parents:
        raise ToolError("invalid_input", "packet.pdfLocalPath must stay inside the Cobros workspace.")
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ToolError("invalid_input", "packet.pdfLocalPath does not point to a spooled PDF.")
    return path.read_bytes()


def external_reply_to(thread: dict[str, Any] | None) -> str | None:
    if not thread:
        return None
    for msg in reversed(thread.get("messages") or []):
        from_email = msg.get("fromEmail")
        if isinstance(from_email, str) and from_email and not from_email.endswith("@owlswatch.com"):
            return from_email
    return None


def gmail_draft_body(fields: dict[str, Any], warnings: list[str]) -> str:
    lines = [
        "Hola,",
        "",
        "Adjuntamos la cuenta de cobro solicitada para revision.",
        "",
        f"Referencia: {fields.get('clientReference') or ''}",
        f"Servicio: {fields.get('concept') or ''} - {fields.get('serviceDates') or ''}",
        f"Valor: COP {int(fields.get('amountCop') or 0):,}",
    ]
    if "rut_or_electronic_invoice_requested" in warnings:
        lines.extend(["", "Nota interna: la solicitud menciona RUT/factura electronica; adjuntar RUT manualmente si corresponde."])
    lines.extend(["", "Saludos,"])
    return "\n".join(lines)


def create_gmail_draft(config: dict[str, Any], prepared: dict[str, Any], packet: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    thread_id = validate_safe_id("threadId", args.get("threadId"), required=False)
    to_email = validate_text("to", args.get("to"), required=False, max_len=240)
    thread = args.get("thread") if isinstance(args.get("thread"), dict) else None
    to_email = to_email or external_reply_to(thread)
    if not to_email or not EMAIL_RE.match(to_email):
        raise ToolError("invalid_input", "A recipient email is required to create a Gmail draft.")
    fields = prepared["fields"]
    subject = validate_text("subject", args.get("subject"), required=False, max_len=300) or f"Cuenta de cobro - {fields.get('clientReference') or fields.get('serviceDates')}"
    body = validate_text("body", args.get("body"), required=False, max_len=50000) or gmail_draft_body(fields, prepared.get("warnings") or [])
    pdf_bytes = read_spooled_pdf(packet)
    msg = EmailMessage()
    msg["To"] = to_email
    msg["From"] = gmail_account(config)
    msg["Subject"] = subject
    if args.get("inReplyTo"):
        msg["In-Reply-To"] = str(args.get("inReplyTo"))
        msg["References"] = str(args.get("inReplyTo"))
    msg.set_content(body)
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=packet.get("pdfFileName") or "cuenta-de-cobro.pdf")
    raw = b64url(msg.as_bytes())
    service = google_build_service(config, "gmail", "v1", ["https://www.googleapis.com/auth/gmail.compose"], gmail_account(config))
    message_body: dict[str, Any] = {"raw": raw}
    if thread_id:
        message_body["threadId"] = thread_id
    draft = service.users().drafts().create(userId="me", body={"message": message_body}).execute()
    draft_id = draft.get("id")
    return {"gmailDraftId": draft_id, "gmailThreadId": thread_id or draft.get("message", {}).get("threadId"), "recipient": to_email, "subject": subject}


def operations_email_intake(config: dict[str, Any], prepared: dict[str, Any], packet: dict[str, Any], draft: dict[str, Any], args: dict[str, Any]) -> dict[str, Any] | None:
    token = operations_email_token(config)
    if not token:
        return None
    fields = prepared["fields"]
    thread_id = draft.get("gmailThreadId") or args.get("threadId") or f"cobros-manual-{hashlib.sha256(json_dumps(fields).encode()).hexdigest()[:16]}"
    payload = {
        "propertyId": "owlswatch",
        "agentId": "cobros",
        "gmail": {
            "account": gmail_account(config),
            "threadId": thread_id,
            "sourceMessageId": args.get("sourceMessageId"),
            "lastMessageId": args.get("sourceMessageId"),
        },
        "thread": {
            "subject": draft.get("subject") or "Cuenta de cobro",
            "clientName": fields.get("clientReference"),
            "clientEmail": draft.get("recipient"),
            "participants": [{"email": draft.get("recipient"), "role": "external"}],
            "detectedLanguage": "es",
            "category": "supplier_or_admin",
            "priority": "normal",
            "summary": f"Cuenta de cobro for {fields.get('debtorLegalName')} / {fields.get('clientReference')} / COP {fields.get('amountCop')}",
            "messages": [],
        },
        "draft": {
            "status": "gmail_draft_created",
            "confidence": "medium",
            "detectedLanguage": "es",
            "toAddresses": [draft.get("recipient")],
            "subject": draft.get("subject"),
            "body": gmail_draft_body(fields, prepared.get("warnings") or []),
        },
        "context": {
            "originalClientQuestion": args.get("sourceSummary") or "Cuenta de cobro request",
            "missingInformationFlags": [],
            "warningFlags": prepared.get("warnings") or [],
            "boundaries": ["Cobros created a Gmail draft with PDF attachment only. Human must review and send from Gmail for v1."],
            "lunaSources": {},
            "lunaContextSummary": "Not applicable.",
            "agentNotes": "Cuenta de cobro packet drafted by Cobros.",
            "agentPayload": {"prepared": prepared, "packet": packet, "gmailDraft": draft},
        },
        "options": {"createGmailDraft": False, "notifyTelegram": False},
    }
    response = http_json(
        f"{operations_base_url(config)}/api/emails/intake",
        payload,
        {"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if response.get("success") is False or response.get("ok") is False:
        raise ToolError("operations_error", "Operations Email Desk intake returned an error.", retryable=False)
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    return {
        "taskId": data.get("taskId") or data.get("id"),
        "taskUrl": data.get("taskUrl"),
        "status": data.get("status"),
    }


def tool_create_gmail_draft(args: dict[str, Any]) -> dict[str, Any]:
    prepared = args.get("prepared")
    packet = args.get("packet")
    if not isinstance(prepared, dict) or not isinstance(packet, dict):
        raise ToolError("invalid_input", "prepared and packet are required objects.")
    if prepared.get("status") != "ready":
        raise ToolError("not_ready", "Gmail draft can only be created from prepared status=ready.")
    config = load_config()
    draft = create_gmail_draft(config, prepared, packet, args)
    operations_task = operations_email_intake(config, prepared, packet, draft, args)
    if operations_task:
        draft["operationsTask"] = operations_task
    else:
        draft["operationsTask"] = {"skipped": True, "reason": "email_agent_token_missing"}
    return {"ok": True, **draft}


def tool_send_telegram_message(args: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    chat_id = str(args.get("chat_id") or cobros_notify_chat_id(config))
    text = validate_text("text", args.get("text"), required=True, max_len=3900)
    thread_id = args.get("message_thread_id") or cobros_notify_thread_id(config)
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if thread_id:
        payload["message_thread_id"] = str(thread_id)
    response = http_json(f"https://api.telegram.org/bot{telegram_token(config)}/sendMessage", payload, {}, timeout=20)
    if not response.get("ok"):
        raise ToolError("telegram_error", "Telegram sendMessage failed.", retryable=True)
    result = response.get("result") or {}
    return {"ok": True, "message_id": result.get("message_id"), "message_thread_id": result.get("message_thread_id")}


def tool_memory_log(args: dict[str, Any]) -> dict[str, Any]:
    content = validate_text("content", args.get("content"), required=True, max_len=3000)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path = MEMORY_DIR / f"{dt.date.today().isoformat()}.md"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"- {now_iso()} {content.strip()}\n")
    return {"ok": True}


TOOLS: dict[str, tuple[str, dict[str, Any], Callable[[dict[str, Any]], dict[str, Any]]]] = {
    "owlswatch_cobros_search_gmail_threads": ("Search read-only Owl's Watch Gmail for cuenta de cobro/accounting requests.", {"type": "object", "properties": {"query": {"type": ["string", "null"]}, "maxResults": {"type": "integer", "minimum": 1, "maximum": 20}}, "additionalProperties": False}, tool_search_gmail_threads),
    "owlswatch_cobros_read_gmail_thread": ("Read one Owl's Watch Gmail thread for cuenta de cobro drafting.", {"type": "object", "properties": {"threadId": {"type": "string"}}, "required": ["threadId"], "additionalProperties": False}, tool_read_gmail_thread),
    "owlswatch_cobros_prepare": ("Extract and validate cuenta de cobro fields. Does not create documents.", {"type": "object", "properties": {"raw_text": {"type": ["string", "null"]}, "thread": {"type": ["object", "null"], "additionalProperties": True}, "source_metadata": {"type": ["object", "null"], "additionalProperties": True}, "human_override": {"type": "boolean"}, "override_fields": {"type": ["object", "null"], "additionalProperties": True}}, "additionalProperties": False}, tool_prepare),
    "owlswatch_cobros_create_packet": ("Create Google Doc cuenta de cobro and exported PDF from a ready prepared result.", {"type": "object", "properties": {"prepared": {"type": "object", "additionalProperties": True}}, "required": ["prepared"], "additionalProperties": False}, tool_create_packet),
    "owlswatch_cobros_create_gmail_draft": ("Create a Gmail draft reply with the cuenta de cobro PDF attached. Never sends.", {"type": "object", "properties": {"prepared": {"type": "object", "additionalProperties": True}, "packet": {"type": "object", "additionalProperties": True}, "thread": {"type": ["object", "null"], "additionalProperties": True}, "threadId": {"type": ["string", "null"]}, "to": {"type": ["string", "null"]}, "subject": {"type": ["string", "null"]}, "body": {"type": ["string", "null"]}, "inReplyTo": {"type": ["string", "null"]}, "sourceMessageId": {"type": ["string", "null"]}, "sourceSummary": {"type": ["string", "null"]}}, "required": ["prepared", "packet"], "additionalProperties": False}, tool_create_gmail_draft),
    "owlswatch_cobros_send_telegram_message": ("Send a short Cobros Telegram notification to the configured Owl's Watch topic.", {"type": "object", "properties": {"text": {"type": "string"}, "chat_id": {"type": ["string", "number", "null"]}, "message_thread_id": {"type": ["string", "number", "null"]}}, "required": ["text"], "additionalProperties": False}, tool_send_telegram_message),
    "owlswatch_cobros_memory_log": ("Append one concise Cobros memory line.", {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"], "additionalProperties": False}, tool_memory_log),
}


def rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        return rpc_result(request_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "owlswatch_cobros", "version": "0.1.0"}})
    if method == "notifications/initialized":
        return None
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
