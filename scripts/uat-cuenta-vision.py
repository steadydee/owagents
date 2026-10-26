#!/usr/bin/env python3
"""Opt-in live vision UAT with synthetic receipts only.

Requires Pillow and the existing OpenAI receipt-provider credential. No real
photos, expense writes, Telegram delivery or agent sessions are used. Each run
uses three fixtures with at most two bounded Responses API reads per fixture.
"""
import base64
import concurrent.futures
import importlib.util
import io
import json
from pathlib import Path
import time

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cuenta_synthetic_vision", ROOT / "tools/owlswatch_intake/server.py"
)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
MONTHS = (
    "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
    "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE",
)


def check(api_key, date_text, expected_date):
    image = Image.new("RGB", (800, 1100), "white")
    drawing = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=30)
    lines = [
        "SYNTHETIC TEST SHOP", "NOT A REAL PURCHASE", "RECIBO DE PRUEBA",
        f"FECHA: {date_text}", "", "Arroz         COP  10.000",
        "Leche         COP   8.000", "Pan           COP   6.000", "",
        "TOTAL         COP  24.000", "GRACIAS",
    ]
    for index, line in enumerate(lines):
        drawing.text((40, 40 + index * 65), line, fill="black", font=font)
    output = io.BytesIO()
    image.save(output, format="PNG")
    url = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
    started = time.monotonic()
    result = server.openai_extract_receipt_with_recheck(
        api_key, "gpt-4.1-mini", "gpt-4.1-mini", [url], None
    )
    _, issues = server.validate_receipt_reading(result)
    assert result["expense_date"] == expected_date, result
    assert result["total_amount"] == 24000, result
    assert result["currency"] == "COP", result
    assert not issues, (issues, result)
    assert result["category"], result
    assert result["category_basis"] == "purchased_items", result
    assert "category_inferred_from_items" in result["flags"], result
    return {
        "fixture": "synthetic-only", "dateFormat": date_text,
        "model": "gpt-4.1-mini", "dateCorrect": True, "amountCorrect": True,
        "currencyCorrect": True, "categoryInferred": True,
        "seconds": round(time.monotonic() - started, 1),
    }


def main():
    api_key, _ = server.vision_config(server.load_config())
    assert api_key, "The existing receipt-provider credential is required."
    current = server.receipt_today()
    printed = [
        current.isoformat(),
        f"{MONTHS[current.month - 1]} {current.day} DE {current.year}",
        f"{current.day}/{current.month}/{current.year % 100:02}",
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(check, api_key, value, current.isoformat()) for value in printed]
        for job in jobs:
            print(json.dumps(job.result()), flush=True)


if __name__ == "__main__":
    main()
