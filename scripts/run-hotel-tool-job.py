#!/usr/bin/env python3
"""Invoke a fixed Hotel scheduled operation; never prints guest data."""
import importlib.util
import contextlib
import json
import os
from pathlib import Path
import re
import sys


RECONCILIATION_REASONS = frozenset({
    "government_outcome_unknown", "government_payload_changed", "journal_invalid",
})
ORDINARY_REVIEW_REASONS = frozenset({
    "sin registro", "sin documentos", "sin reserva vinculada", "needs_info",
})
EXTRACTION_REVIEW_PATTERN = re.compile(
    r"(?:falta documento/registro de 1 huesped|faltan documentos/registros de [1-9][0-9]* huespedes|"
    r"1 huesped necesita revision de extraccion|[1-9][0-9]* huespedes necesitan revision de extraccion)"
)


def ordinary_review(reason):
    if not isinstance(reason, str):
        return False
    return reason in ORDINARY_REVIEW_REASONS or all(
        EXTRACTION_REVIEW_PATTERN.fullmatch(part) for part in reason.split("; ")
    )


def safe_summary(result):
    """Accept the deterministic pickup contract; emit only fixed codes/counts."""
    if not isinstance(result, dict):
        raise ValueError("Invalid pickup result")
    for key in ("processed", "needsReview", "errors"):
        if not isinstance(result.get(key), list) or any(not isinstance(row, dict) for row in result[key]):
            raise ValueError("Invalid pickup rows")
    remaining = result.get("remainingCount", 0)
    if isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 0:
        raise ValueError("Invalid pickup count")
    if not isinstance(result.get("truncated", False), bool):
        raise ValueError("Invalid truncation flag")
    rows = result["needsReview"] + result["errors"]
    unknown = sum(row.get("reason") in RECONCILIATION_REASONS for row in rows)
    receipt_pending = sum(row.get("reason") == "government_receipt_pending" for row in rows)
    in_progress = sum(row.get("reason") == "operation_in_progress" for row in rows)
    # Pickup currently flattens multi-portal outcomes to the first reason. A
    # later portal failure can therefore arrive as generic "blocked". Only
    # known missing-document/extraction reviews count as a completed sweep.
    unresolved = sum(not ordinary_review(row.get("reason")) for row in result["needsReview"])
    telegram_failed = isinstance(result.get("telegram"), dict) and result["telegram"].get("ok") is False
    success = (
        result.get("ok") is True and not result["errors"]
        and not result.get("truncated") and remaining == 0
        and not unknown and not receipt_pending and not in_progress and not unresolved
        and not telegram_failed and not result.get("alertStateWarning")
    )
    code = "completed" if success else (
        "reconciliation_required" if unknown else
        "receipt_recording_pending" if receipt_pending else
        "operation_in_progress" if in_progress else
        "incomplete_batch" if result.get("truncated") or remaining else "tool_job_failed"
    )
    return {
        "job": "registro_pickup", "success": success, "code": code,
        "processed": len(result["processed"]), "needsReview": len(result["needsReview"]),
        "errors": len(result["errors"]), "remaining": remaining,
        "reconciliationRequired": unknown, "receiptPending": receipt_pending,
        "inProgress": in_progress, "unresolved": unresolved,
        # The runner never retries in-process. A later run can safely resume
        # known receipts; it cannot override an unknown provider outcome.
        "retryable": not unknown and bool(receipt_pending or in_progress)
        and unresolved <= receipt_pending + in_progress and not result["errors"],
    }


def main():
    workspace = Path(os.environ.get("HOTEL_WORKSPACE", "~/.openclaw/workspace-hotel-ops")).expanduser()
    os.environ["HOTEL_PMS_WORKSPACE"] = str(workspace)
    path = workspace / "tools/hotel_pms/server.py"
    try:
        # Provider libraries and imports must not print guest fields or raw
        # responses into the scheduler log, including when they raise.
        with open(os.devnull, "w") as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            spec = importlib.util.spec_from_file_location("hotel_scheduled_tools", path)
            server = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(server)
            result = server.tool_hotel_registro_daily_pickup({"submitTra": True, "notify": True, "maxRecords": 25, "daysBack": 7, "daysAhead": 2})
            summary = safe_summary(result)
        print(json.dumps(summary))
        return 0 if summary["success"] else 1
    except Exception:
        print(json.dumps({"job": "registro_pickup", "success": False, "code": "tool_job_failed", "action": "Inspect the durable submission journal before retrying an unknown outcome."}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
