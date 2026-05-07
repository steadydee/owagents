import importlib.util
import os
from pathlib import Path


SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("owlswatch_quotes_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(server)


def juan_manuel_payload():
    return {
        "quoteType": "operator",
        "arrivalDate": "2026-02-05",
        "departureDate": "2026-02-05",
        "guestCount": 5,
        "lodging": {"cabins": 0, "nights": 0},
        "meals": {"plan": "breakfast_and_lunch"},
        "activities": [{"type": "morning_bird_tour", "quantity": 1, "guide": "local_spanish"}],
        "staff": {"guides": 1, "meals": {"breakfast": True, "lunch": True}},
        "transport": {"included": False},
        "requestSummary": (
            "February 05/ 2026. 5 clients. operator Juan Manuel. "
            "1 Guide. Breakfast and lunch please. Birding tour day trip."
        ),
    }


def test_day_trip_meal_plan_includes_client_breakfast_and_no_lodging():
    normalized = server.normalize_calculate_payload(juan_manuel_payload())

    assert normalized["lodging"]["requested"] is False
    assert normalized["lodging"]["cabinCount"] == 0
    assert normalized["meals"] == {"breakfasts": 1, "lunches": 1, "dinners": 0}
    assert normalized["birding"]["morningTourDays"] == 1


def test_cabin_full_board_excludes_checkout_lunch_by_default():
    normalized = server.normalize_calculate_payload(
        {
            "quoteType": "operator",
            "agencyName": "Promotora Neptuno",
            "clientName": "Trachsel x2",
            "arrivalDate": "2026-12-28",
            "departureDate": "2026-12-31",
            "guestCount": 2,
            "lodging": {"requested": True, "cabinCount": 1},
            "meals": {"plan": "full_board"},
            "requestSummary": "3 nights. Habitacion Cabana - Alimentacion Completa.",
        }
    )

    assert normalized["nights"] == 3
    assert normalized["meals"] == {"breakfasts": "included_with_lodging", "lunches": 2, "dinners": 3}


def test_cabin_spanish_alimentacion_completa_counts_full_board():
    normalized = server.normalize_calculate_payload(
        {
            "quoteType": "operator",
            "agencyName": "Promotora Neptuno",
            "clientName": "Trachsel x2",
            "arrivalDate": "2026-12-28",
            "departureDate": "2026-12-31",
            "guestCount": 2,
            "lodging": {"requested": True, "cabinCount": 1},
            "requestSummary": "Habitación Cabaña -Aalimentación Completa. 3 noches.",
        }
    )

    assert normalized["meals"] == {"breakfasts": "included_with_lodging", "lunches": 2, "dinners": 3}


def test_cabin_full_board_includes_checkout_lunch_only_when_explicit():
    normalized = server.normalize_calculate_payload(
        {
            "quoteType": "operator",
            "arrivalDate": "2026-12-28",
            "departureDate": "2026-12-31",
            "guestCount": 2,
            "lodging": {"requested": True, "cabinCount": 1},
            "meals": {"plan": "full_board"},
            "requestSummary": "3 nights full board, please include lunch on checkout day.",
        }
    )

    assert normalized["meals"] == {"breakfasts": "included_with_lodging", "lunches": 3, "dinners": 3}


def test_day_trip_display_total_includes_staff_meals_and_operator_tour_discount():
    payload = juan_manuel_payload()
    calc = {
        "currency": "COP",
        "pricebookVersion": "Owl's Watch 2026 Operator Rates",
        "lineItems": [
            {
                "serviceCode": "breakfast",
                "description": "Breakfast",
                "category": "restaurant",
                "unitPriceCop": 55000,
                "quantity": 5,
                "totalCop": 275000,
            },
            {
                "serviceCode": "lunch",
                "description": "Lunch",
                "category": "restaurant",
                "unitPriceCop": 65000,
                "quantity": 5,
                "totalCop": 325000,
            },
            {
                "serviceCode": "bird_tour",
                "description": "Photography and bird tour",
                "category": "activity",
                "unitPriceCop": 150000,
                "quantity": 5,
                "totalCop": 750000,
            },
        ],
        "subtotalCop": 1350000,
        "discountCop": 0,
        "totalCop": 1350000,
        "assumptions": [],
    }

    displayed = server.apply_quote_display_policies(payload, calc)

    assert displayed["subtotalCop"] == 1375000
    assert displayed["discountCop"] == 75000
    assert displayed["totalCop"] == 1300000
    descriptions = [item["description"] for item in displayed["lineItems"]]
    assert "Guide/Driver Breakfast" in descriptions
    assert "Guide/Driver Lunch" in descriptions


def test_incomplete_draft_blocks_before_operations_row():
    payload = juan_manuel_payload()
    data = {
        **payload,
        "agencyName": "Juan Manuel",
        "clientName": None,
        "calculation": {
            "lineItems": [
                {
                    "serviceCode": "lunch",
                    "description": "Lunch",
                    "category": "restaurant",
                    "unitPriceCop": 65000,
                    "quantity": 5,
                    "totalCop": 325000,
                },
                {
                    "serviceCode": "bird_tour",
                    "description": "Photography and bird tour",
                    "category": "activity",
                    "unitPriceCop": 150000,
                    "quantity": 5,
                    "totalCop": 750000,
                },
            ],
            "subtotalCop": 1075000,
            "discountCop": 75000,
            "totalCop": 1000000,
        },
    }

    missing = server.sheet_blocking_missing_details(data)

    assert "client name" not in missing
    assert "client breakfast line item" in missing


def test_quote_file_name_skips_missing_client():
    assert (
        server.quote_file_name(
            {
                "quoteNumber": "Q-2026-0011",
                "agencyName": "Juan Manuel",
                "clientName": None,
                "arrivalDate": "2026-02-05",
                "departureDate": "2026-02-05",
            }
        )
        == "Juan Manuel - Feb 5 2026 - Q-2026-0011"
    )


def test_model_variant_payload_with_operator_or_direct_and_nested_meals():
    normalized = server.normalize_calculate_payload(
        {
            "operator_or_direct": "operator",
            "request_type": "birding_day_trip",
            "agencyName": "Juan Manuel",
            "arrivalDate": "2026-02-05",
            "departureDate": "2026-02-05",
            "guestCount": 5,
            "birding": {"days": 1, "guideType": "local_spanish", "guideCount": 1},
            "meals": {"breakfast": {"clients": 5, "guides": 1}, "lunch": {"clients": 5, "guides": 1}},
            "transport": {"included": False},
        }
    )

    assert normalized["audience"] == "operator"
    assert normalized["lodging"]["requested"] is False
    assert normalized["meals"] == {"breakfasts": 1, "lunches": 1, "dinners": 0}
    assert normalized["birding"]["morningTourDays"] == 1
    assert normalized["guests"]["guides"] == 1


def test_model_variant_payload_with_services_meals_uses_guest_days():
    normalized = server.normalize_calculate_payload(
        {
            "quoteType": "operator",
            "audience": "operator",
            "operatorName": "Juan Manuel",
            "visitType": "birding_day_trip",
            "startDate": "2026-02-05",
            "endDate": "2026-02-05",
            "guestCount": 5,
            "services": [
                {"type": "birding_day_trip", "quantity": 5, "days": 1},
                {"type": "guide", "quantity": 1, "days": 1},
                {"type": "breakfast", "quantity": 6},
                {"type": "lunch", "quantity": 6},
            ],
            "includeBreakfast": True,
            "includeLunch": True,
            "guideCount": 1,
            "transportIncluded": False,
        }
    )

    assert normalized["arrivalDate"] == "2026-02-05"
    assert normalized["departureDate"] == "2026-02-05"
    assert normalized["meals"] == {"breakfasts": 1, "lunches": 1, "dinners": 0}


def test_raw_text_intent_does_not_parse_clients_as_client_name():
    intent = server.raw_text_quote_intent(
        "February 05/2026\n5 clients\noperator Juan Manuel\n1 Guide birding day trip\nBreakfast and lunch please"
    )

    assert intent.get("clientName") is None
    assert intent["agencyName"] == "Juan Manuel"
    assert intent["guestCount"] == 5


def test_prepare_and_create_mock_high_level_flow():
    previous = os.environ.get("OWLSWATCH_QUOTES_MOCKS")
    os.environ["OWLSWATCH_QUOTES_MOCKS"] = "1"
    try:
        prepared = server.tool_quote_prepare(
            {
                "raw_text": "February 05/2026\n5 clients\noperator Juan Manuel\n1 Guide birding day trip\nBreakfast and lunch please",
                "source_metadata": {"source": "telegram", "chatId": "telegram:-1003949383737", "messageId": "unit-high-level", "topicId": "3"},
                "mode": "draft",
            }
        )
        assert prepared["status"] == "ready_preview"
        assert prepared["calculation"]["totalCop"] == 1300000

        created = server.tool_quote_create_draft(
            {
                "prepared_quote": prepared["preparedQuote"],
                "source_metadata": {"source": "telegram", "chatId": "telegram:-1003949383737", "messageId": "unit-high-level", "topicId": "3"},
            }
        )
        assert created["ok"] is True
        assert created["totalCop"] == 1300000
        assert created.get("driveSheetUrl")
    finally:
        if previous is None:
            os.environ.pop("OWLSWATCH_QUOTES_MOCKS", None)
        else:
            os.environ["OWLSWATCH_QUOTES_MOCKS"] = previous


def test_high_level_create_uses_quote_rule_version_in_idempotency_key():
    previous = os.environ.get("OWLSWATCH_QUOTES_MOCKS")
    os.environ["OWLSWATCH_QUOTES_MOCKS"] = "1"
    try:
        prepared = server.tool_quote_prepare(
            {
                "raw_text": "Operator Promotora Neptuno. Client Trachsel x2. 2 guests. 1 cabin. Dec 28-31, 2026. Alimentacion Completa.",
                "source_metadata": {"source": "gmail", "threadId": "thread-neptuno-trachsel"},
                "mode": "draft",
            }
        )
        created = server.tool_quote_create_draft(
            {
                "prepared_quote": prepared["preparedQuote"],
                "source_metadata": {"source": "gmail", "threadId": "thread-neptuno-trachsel"},
            }
        )
        key = created.get("payload", {}).get("idempotencyKey") if isinstance(created.get("payload"), dict) else None
        if key is None:
            quote_id = created["quoteId"]
            record_path = server.MOCK_QUOTES_DIR / f"{quote_id}.json"
            import json
            key = json.loads(record_path.read_text())["payload"]["idempotencyKey"]

        assert f"rules-{server.normalize_id_part(server.QUOTE_RULE_VERSION)}" in key
    finally:
        if previous is None:
            os.environ.pop("OWLSWATCH_QUOTES_MOCKS", None)
        else:
            os.environ["OWLSWATCH_QUOTES_MOCKS"] = previous


def test_redo_creates_distinct_mock_draft_from_same_source():
    previous = os.environ.get("OWLSWATCH_QUOTES_MOCKS")
    os.environ["OWLSWATCH_QUOTES_MOCKS"] = "1"
    try:
        prepared = server.tool_quote_prepare(
            {
                "raw_text": "Operator Promotora Neptuno. Client Trachsel x2. 2 guests. 1 cabin. Dec 28-31, 2026. Alimentacion Completa.",
                "source_metadata": {"source": "gmail", "threadId": "thread-neptuno-redo"},
                "mode": "draft",
            }
        )
        first = server.tool_quote_create_draft(
            {
                "prepared_quote": prepared["preparedQuote"],
                "source_metadata": {"source": "gmail", "threadId": "thread-neptuno-redo"},
            }
        )
        second = server.tool_quote_create_draft(
            {
                "prepared_quote": prepared["preparedQuote"],
                "source_metadata": {"source": "gmail", "threadId": "thread-neptuno-redo", "redoKey": "manual-redo"},
                "redo": True,
            }
        )

        assert first["quoteId"] != second["quoteId"]
        assert second["redo"] is True
    finally:
        if previous is None:
            os.environ.pop("OWLSWATCH_QUOTES_MOCKS", None)
        else:
            os.environ["OWLSWATCH_QUOTES_MOCKS"] = previous


def test_sheet_values_group_cabin_quote_by_day_without_checkout_lunch():
    data = {
        "quoteNumber": "Q-2026-0013",
        "agencyName": "Promotora Neptuno",
        "clientName": "Trachsel x2",
        "arrivalDate": "2026-12-28",
        "departureDate": "2026-12-31",
        "guestCount": 2,
        "requestSummary": "3 nights. Cabin stay. Alimentacion Completa.",
        "calculation": {
            "currency": "COP",
            "lineItems": [
                {"description": "Forest Cabin", "notes": "Breakfast included for two guests.", "unitPriceCop": 840000, "quantity": 3, "totalCop": 2520000},
                {"description": "Client Lunch", "unitPriceCop": 65000, "quantity": 4, "totalCop": 260000},
                {"description": "Client Dinner", "unitPriceCop": 65000, "quantity": 6, "totalCop": 390000},
            ],
            "subtotalCop": 3170000,
            "discountCop": 252000,
            "totalCop": 2918000,
        },
    }

    quote_rows = server.sheet_values(data)["Quote"]
    day_rows = [row[0] for row in quote_rows if server.is_day_header_row(row)]

    assert day_rows == ["Dec 28 2026", "Dec 29 2026", "Dec 30 2026", "Dec 31 2026"]
    dec29_index = next(i for i, row in enumerate(quote_rows) if row[0] == "Dec 29 2026")
    assert quote_rows[dec29_index + 1][0] == "Client Breakfast"
    assert quote_rows[dec29_index + 1][2] == 0
    assert quote_rows[dec29_index + 1][4] == 0
    assert quote_rows[dec29_index + 2][0] == "Client Lunch"
    assert quote_rows[dec29_index + 2][3] == 2
    dec31_index = next(i for i, row in enumerate(quote_rows) if row[0] == "Dec 31 2026")
    assert quote_rows[dec31_index + 1][0] == "Client Breakfast"
    assert all(row[0] != "Client Lunch" for row in quote_rows[dec31_index + 1:dec31_index + 3])


def test_revise_mock_draft_removes_two_lunches_and_creates_new_revision():
    previous = os.environ.get("OWLSWATCH_QUOTES_MOCKS")
    os.environ["OWLSWATCH_QUOTES_MOCKS"] = "1"
    try:
        payload = {
            "sourceType": "MANUAL",
            "sourceId": "revision-unit-source",
            "agencyName": "Promotora Neptuno",
            "clientName": "Trachsel x2",
            "audience": "OPERATOR",
            "arrivalDate": "2026-12-28",
            "departureDate": "2026-12-31",
            "guestCount": 2,
            "requestSummary": "3 nights. Cabin stay. Alimentacion Completa.",
            "calculation": {
                "currency": "COP",
                "lineItems": [
                    {"description": "Forest Cabin", "notes": "Breakfast included for two guests.", "unitPriceCop": 840000, "quantity": 3, "totalCop": 2520000},
                    {"description": "Client Lunch", "unitPriceCop": 65000, "quantity": 6, "totalCop": 390000},
                    {"description": "Client Dinner", "unitPriceCop": 65000, "quantity": 6, "totalCop": 390000},
                ],
                "subtotalCop": 3300000,
                "discountCop": 252000,
                "totalCop": 3048000,
            },
            "idempotencyKey": "revision-unit-original",
        }
        original = server.tool_quote_create_draft({"payload": payload})
        revised = server.tool_quote_revise_draft({
            "quote_ref": original["quoteNumber"],
            "instruction": "remove 2 lunches",
            "source_metadata": {"source": "telegram", "messageId": "revision-test"},
        })
        quote_id = revised["quoteId"]
        import json
        record = json.loads((server.MOCK_QUOTES_DIR / f"{quote_id}.json").read_text())
        items = record["payload"]["calculation"]["lineItems"]
        lunch = next(item for item in items if "Lunch" in item["description"])

        assert revised["revisionOfQuoteNumber"] == original["quoteNumber"]
        assert revised["quoteId"] != original["quoteId"]
        assert lunch["quantity"] == 4
        assert record["payload"]["calculation"]["totalCop"] == 2918000
    finally:
        if previous is None:
            os.environ.pop("OWLSWATCH_QUOTES_MOCKS", None)
        else:
            os.environ["OWLSWATCH_QUOTES_MOCKS"] = previous
