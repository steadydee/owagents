"""Runtime-only allowlists and host provenance; no model approval switches."""
import json
import os
from pathlib import Path
import re
import stat
import time


class BoundaryError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


def private_file(path, root):
    path, root = Path(path), Path(root).resolve()
    if not path.is_absolute():
        path = root / path
    try:
        path.resolve().relative_to(root)
        # Reject symlinks even within the root: no ambiguous config/key identity.
        for item in [path] + list(path.parents):
            if item == root.parent:
                break
            if item.is_symlink():
                raise ValueError()
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError()
    except (OSError, ValueError):
        raise BoundaryError("PRIVATE_FILE_REQUIRED", "A required private runtime file is missing or has unsafe permissions.")
    return path.resolve()


def runtime_config():
    raw_root = os.environ.get("OWLSWATCH_PAYROLL_WORKSPACE")
    if not raw_root:
        raise BoundaryError("NOT_CONFIGURED", "Nómina workspace and authorized Telegram route are not configured.")
    root = Path(raw_root).expanduser()
    if root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o077 or root.stat().st_uid != os.getuid():
        raise BoundaryError("PRIVATE_WORKSPACE_REQUIRED", "The payroll workspace must be a private directory (mode 700).")
    root = root.resolve()
    config_path = private_file(os.environ.get("OWLSWATCH_PAYROLL_CONFIG", "payroll-config.json"), root)
    try:
        config = json.loads(config_path.read_text())
        if not isinstance(config, dict) or type(config.get("schema_version")) is not int:
            raise ValueError()
        if config.get("schema_version") != 1 or config.get("enabled") is not True:
            raise BoundaryError("NOT_ENABLED", "Nómina is not enabled. Finish the private payroll setup first.")
        telegram = config["telegram"]
        if not isinstance(telegram, dict):
            raise ValueError()
        if not isinstance(telegram["account_id"], str) or not telegram["account_id"] or len(telegram["account_id"]) > 80:
            raise ValueError()
        if not isinstance(telegram["allowed_sender_ids"], list) or not isinstance(telegram["allowed_routes"], list) or not telegram["allowed_sender_ids"] or not telegram["allowed_routes"]:
            raise ValueError()
        if any(not isinstance(s, str) or not re.fullmatch(r"[0-9]{1,20}", s) for s in telegram["allowed_sender_ids"]):
            raise ValueError()
        for route in telegram["allowed_routes"]:
            if not isinstance(route, dict) or set(route) != {"chat_id", "thread_id"} or not isinstance(route["chat_id"], str) or not isinstance(route["thread_id"], str) or not re.fullmatch(r"-?[0-9]{1,20}", route["chat_id"]) or not re.fullmatch(r"[0-9]{0,20}", route["thread_id"]):
                raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise BoundaryError("INVALID_CONFIG", "Payroll runtime configuration is incomplete or invalid.")
    return root, config


def trusted_actor(config, native=False):
    try:
        actor = json.loads(os.environ.get("OWLSWATCH_PAYROLL_TRUSTED_CONTEXT", "{}"))
        if not isinstance(actor, dict):
            raise ValueError()
        tg = config["telegram"]
        if actor.get("channel") != "telegram" or actor.get("agentId") != "nomina":
            raise ValueError()
        if actor.get("accountId") != tg["account_id"] or actor.get("senderId") not in tg["allowed_sender_ids"]:
            raise ValueError()
        if {"chat_id": actor.get("chatId"), "thread_id": actor.get("threadId")} not in tg["allowed_routes"]:
            raise ValueError()
        if not isinstance(actor.get("sessionKey"), str) or not 1 <= len(actor["sessionKey"]) <= 500:
            raise ValueError()
        expected = "native_command" if native else "tool_context"
        if actor.get("source") != expected:
            raise ValueError()
        if native:
            approved_at = actor.get("approvedAt")
            if type(approved_at) not in (int, float) or not time.time() - 300 <= approved_at <= time.time() + 30:
                raise ValueError()
            if actor.get("authorized") is not True or not re.fullmatch(r"[a-fA-F0-9-]{36}", actor.get("approvalEventId", "")):
                raise ValueError()
    except (TypeError, ValueError, KeyError):
        raise BoundaryError("UNAUTHORIZED", "This Telegram sender or payroll conversation is not authorized.")
    # Only host fields; external input cannot add flags or alter this object.
    keys = ("channel", "agentId", "senderId", "chatId", "threadId", "accountId", "sessionKey",
            "source", "authorized", "approvedAt", "approvalEventId", "sourceEventId")
    return {key: actor[key] for key in keys if key in actor}


def private_directory(path, root):
    path, root = Path(path), Path(root).resolve()
    try:
        path.resolve().relative_to(root)
        if path.is_symlink():
            raise ValueError()
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid():
            raise ValueError()
    except (ValueError, OSError):
        raise BoundaryError("PRIVATE_DIRECTORY_REQUIRED", "Payroll state directories must be private and inside the workspace.")
    return path
