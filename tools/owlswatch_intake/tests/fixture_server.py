"""Offline tool double for real-model UAT. No network or application credentials."""
import json
import os
import sys
from pathlib import Path

name = sys.argv[2]
args = json.load(sys.stdin)
with Path(os.environ["CUENTA_UAT_CALLS"]).open("a") as log:
    log.write(json.dumps({"name": name, "args": args}) + "\n")

results = {
    "owlswatch_telegram_get_file": {"file_path": "photos/test.jpg"},
    "owlswatch_telegram_download_file": {"local_path": "/receipt-spool/test/original-1.jpg"},
    "owlswatch_telegram_send_chat_action": {"ok": True},
    "owlswatch_operations_upload_attachment": {"attachments": [{
        "url": "https://receipts.example.test/test.jpg", "fileName": "original-1.jpg",
        "contentType": "image/jpeg", "sizeBytes": 1234,
    }]},
    "owlswatch_vision_extract_receipt": {
        "vendor_name": "Test Market", "expense_date": "2026-09-18", "currency": "COP",
        "total_amount": 33000, "category": "food_groceries", "confidence": 0.98,
        "flags": [], "raw_ocr_text": "Test Market 2026-09-18 COP 33000",
    },
    "owlswatch_operations_create_expense_draft": {
        "ok": True, "expense_id": "test-expense", "status": "draft",
        "review_url": "https://operations.example.test/expenses/test-expense",
    },
    "owlswatch_memory_log": {"ok": True},
}
if os.environ.get("CUENTA_UAT_SCENARIO") == "vision_failure":
    results["owlswatch_vision_extract_receipt"] = {
        "ok": False, "error": {"code": "vision_error", "message": "Test outage", "retryable": True},
    }
print(json.dumps(results.get(name, {"ok": False, "error": {"code": "unexpected_tool"}})))
