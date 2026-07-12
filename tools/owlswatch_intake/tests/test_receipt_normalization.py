import importlib.util
import unittest
from pathlib import Path


SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("owlswatch_intake_server", SERVER_PATH)
assert SPEC and SPEC.loader
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


class ReceiptNormalizationTests(unittest.TestCase):
    def test_carnicos_is_food_and_groceries(self):
        result = SERVER.normalize_receipt_extraction(
            {
                "vendor_name": "Cárnicos El Hato Sas",
                "expense_date": "2026-07-11",
                "currency": "COP",
                "total_amount": 189245,
                "tax_amount": None,
                "category": "Cárnicos el hato",
                "confidence": 95,
                "flags": ["partial", "openai_vision", "tax not found"],
                "raw_ocr_text": "Producto destino\nCarnicos El Hato Sas\nCorriente - Bancolombia",
                "extraction_status": "succeeded",
            },
            caption="Cárnicos el hato",
        )

        self.assertEqual(result["category"], "Food & Groceries")
        self.assertEqual(result["vendor_name"], "Cárnicos El Hato Sas")
        self.assertEqual(result["flags"], [])

    def test_transfer_uses_payee_and_caption_category(self):
        result = SERVER.normalize_receipt_extraction(
            {
                "vendor_name": "Nequi",
                "expense_date": "2026-07-03",
                "currency": "COP",
                "total_amount": 400000,
                "tax_amount": None,
                "category": None,
                "confidence": 92,
                "flags": ["missing category"],
                "raw_ocr_text": "Transferencia exitosa\nEnviado a JUAN PEREZ\nValor de la transferencia $ 400.000",
                "extraction_status": "partial",
            },
            caption="Resto salario Juan",
        )

        self.assertEqual(result["vendor_name"], "JUAN PEREZ")
        self.assertEqual(result["category"], "Payroll / Contractors")
        self.assertEqual(result["flags"], [])

    def test_propane_and_vehicle_fuel_are_distinct(self):
        self.assertEqual(
            SERVER.canonical_expense_category(None, "Pipas de gas para la cocina", None, None),
            "Utilities",
        )
        self.assertEqual(
            SERVER.canonical_expense_category(None, "Gasolina para el carro", None, None),
            "Transportation & Fuel",
        )

    def test_unknown_category_falls_back_to_other(self):
        self.assertEqual(
            SERVER.canonical_expense_category(None, None, "Proveedor sin regla", "Comprobante 123"),
            "Other",
        )

    def test_lossless_intake_payload_from_extraction(self):
        payload = SERVER.normalize_expense_draft_payload(
            {"operations": {"propertyId": "owlswatch"}},
            {
                "source": "telegram",
                "sourceMessageId": "515",
                "idempotencyKey": "telegram-chat-515",
                "submittedBy": "Adriana Valencia",
                "userCaption": "Cárnicos el hato",
                "receiptExtraction": {
                    "vendor_name": "Cárnicos El Hato Sas",
                    "expense_date": "2026-07-11",
                    "currency": "COP",
                    "total_amount": 189245,
                    "tax_amount": None,
                    "category": "Food & Groceries",
                    "confidence": 95,
                    "flags": ["openai_vision"],
                    "raw_ocr_text": "Producto destino Cárnicos El Hato Sas",
                    "extraction_status": "succeeded",
                },
                "expense": {},
                "attachments": [],
            },
        )

        self.assertEqual(payload["propertyId"], "owlswatch")
        self.assertEqual(payload["expense"]["vendor_name"], "Cárnicos El Hato Sas")
        self.assertEqual(payload["expense"]["category"], "Food & Groceries")
        self.assertEqual(payload["expense"]["currency"], "COP")
        self.assertEqual(payload["expense"]["notes"], "Cárnicos el hato")
        self.assertEqual(payload["agent"]["confidence"], 95)
        self.assertEqual(payload["agent"]["rawOcrText"], "Producto destino Cárnicos El Hato Sas")
        self.assertEqual(payload["flags"], [])

    def test_current_style_payload_is_still_normalized(self):
        payload = SERVER.normalize_expense_draft_payload(
            {"operations": {"propertyId": "owlswatch"}},
            {
                "source": "telegram",
                "idempotencyKey": "telegram-chat-9",
                "expense": {
                    "vendorName": "Bre-B",
                    "date": "2026-07-09",
                    "totalAmount": 212000,
                    "category": None,
                    "note": "Pipas gas. Paid to Juan Jhosain Arango Lopez",
                },
                "flags": ["missing_category", "openai_vision"],
                "attachments": [],
            },
        )

        self.assertEqual(payload["expense"]["vendor_name"], "Juan Jhosain Arango Lopez")
        self.assertEqual(payload["expense"]["category"], "Utilities")
        self.assertEqual(payload["expense"]["notes"], "Pipas gas. Paid to Juan Jhosain Arango Lopez")
        self.assertEqual(payload["flags"], [])


if __name__ == "__main__":
    unittest.main()
