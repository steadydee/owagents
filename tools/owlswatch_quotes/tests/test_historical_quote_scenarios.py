import importlib.util
import os
from pathlib import Path


SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("owlswatch_quotes_server_history", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(server)


def prepare(raw_text: str) -> dict:
    previous = os.environ.get("OWLSWATCH_QUOTES_MOCKS")
    os.environ["OWLSWATCH_QUOTES_MOCKS"] = "1"
    try:
        return server.tool_quote_prepare({
            "raw_text": raw_text,
            "source_metadata": {"source": "historical-scenario-test", "messageId": "history"},
            "mode": "draft",
        })
    finally:
        if previous is None:
            os.environ.pop("OWLSWATCH_QUOTES_MOCKS", None)
        else:
            os.environ["OWLSWATCH_QUOTES_MOCKS"] = previous


def line_items(result: dict) -> list[dict]:
    return result.get("calculation", {}).get("lineItems", [])


def descriptions(result: dict) -> list[str]:
    return [str(item.get("description") or "") for item in line_items(result)]


def assert_ready(result: dict) -> None:
    assert result["ok"] is True
    assert result["status"] == "ready_preview"


def test_operator_day_trip_no_client_with_guide_breakfast_lunch():
    result = prepare(
        "February 05/2026\n"
        "5 clients\n"
        "operator Juan Manuel\n"
        "1 Guide birding day trip\n"
        "Breakfast and lunch please"
    )

    assert_ready(result)
    assert result["canonicalPayload"]["lodging"]["requested"] is False
    assert result["canonicalPayload"]["guests"]["adults"] == 5
    assert "Guide Breakfast" in descriptions(result)
    assert "Guide Lunch" in descriptions(result)
    assert result["calculation"]["totalCop"] == 1300000


def test_operator_day_trip_lunch_only_with_guide_and_driver():
    result = prepare(
        "Feb 5 2026. Operator Juan Manuel. 5 clients. 1 guide and 1 driver. "
        "Birding day trip. Lunch please."
    )

    assert_ready(result)
    assert result["canonicalPayload"]["meals"] == {"breakfasts": 0, "lunches": 1, "dinners": 0}
    assert "Guide/Driver Breakfast" not in descriptions(result)
    assert "Guide/Driver Lunch" in descriptions(result)
    assert result["calculation"]["totalCop"] == 1050000


def test_operator_day_trip_trip_leaders_get_meals_not_tours():
    result = prepare(
        "Sep 4 2026. Operator Birding By Bus. 10 clients, 2 trip leaders, "
        "1 guide, 1 driver. Birding day trip. Breakfast and lunch please."
    )

    assert_ready(result)
    assert result["canonicalPayload"]["guests"]["adults"] == 10
    assert "Trip Leader Breakfast" in descriptions(result)
    assert "Trip Leader Lunch" in descriptions(result)
    bird_tour = next(item for item in line_items(result) if "bird" in str(item.get("description", "")).lower())
    assert bird_tour["quantity"] == 10
    assert result["calculation"]["totalCop"] == 2840000


def test_direct_cross_month_cabin_transport_quote_parses_dates_guests_and_cabins():
    result = prepare(
        "Direct client Patti. Aug 31-Sep 4 2026. 3 adults. 2 cabins. Cabin stay. "
        "Lunch and dinner each day. Birding every day. Transportation from Montezuma and to La Nubia."
    )

    assert_ready(result)
    canonical = result["canonicalPayload"]
    assert canonical["audience"] == "direct"
    assert canonical["arrivalDate"] == "2026-08-31"
    assert canonical["departureDate"] == "2026-09-04"
    assert canonical["nights"] == 4
    assert canonical["guests"]["adults"] == 3
    assert canonical["lodging"]["cabinCount"] == 2
    assert canonical["transport"]["requested"] is True


def test_operator_cabin_with_two_cabins_guide_room_meals_and_birding():
    result = prepare(
        "Operator Jaguarundi Travel. Client Rene. Dec 29-31 2026. "
        "3 clients plus 1 guide. 2 cabins and guide room. Cabin stay with dinner arrival day, "
        "lunch and dinner next day, morning bird tour one day. No transport."
    )

    assert_ready(result)
    canonical = result["canonicalPayload"]
    assert canonical["lodging"]["cabinCount"] == 2
    assert canonical["guests"]["adults"] == 3
    assert canonical["guests"]["guides"] == 1
    assert canonical["birding"]["morningTourDays"] == 1
    assert "Guide Breakfast" in descriptions(result)
    assert "Guide Lunch" in descriptions(result)


def test_operator_reservation_form_without_operator_name_can_draft():
    result = prepare(
        "HOTEL Owl Watch\n"
        "ESTADO Reservacion\n"
        "REFERENCIA - Turrian\n"
        "2 PAX  + Guia\n"
        "FECHA IN - 04 febrero 2027\n"
        "FECHA OUT - 06 febrero 2027 (2 noches)\n"
        "SERVICIO - 1 habitacion matrimonial para pasajero + "
        "1 habitacion single para Guia + desayunos + cenas"
    )

    assert_ready(result)
    canonical = result["canonicalPayload"]
    assert canonical["audience"] == "operator"
    assert result["preparedQuote"]["intent"]["clientName"] == "Turrian"
    assert canonical["arrivalDate"] == "2027-02-04"
    assert canonical["departureDate"] == "2027-02-06"
    assert canonical["lodging"]["guideRoomCount"] == 1
    assert "Guide room" in descriptions(result)
    assert "Guide Lunch" not in descriptions(result)
    assert "Driver" not in " ".join(descriptions(result))
    assert "operator name" in result["missingOptional"]


def test_bilingual_no_cabin_quote_does_not_turn_into_lodging():
    result = prepare(
        "Operator The Colombian Project. Client Kaye Westmark. July 3-6 2026. "
        "2 adults. Bilingual guide for night tour July 3 and morning bird tour July 4. No cabin."
    )

    assert_ready(result)
    assert result["canonicalPayload"]["lodging"]["requested"] is False
    assert result["canonicalPayload"]["birding"]["bilingualGuide"] == "half_day"


def test_unknown_audience_asks_one_question_before_pricing():
    result = prepare("May 13-17 2026. 2 adults. 1 cabin. Full board. Birding every day.")

    assert result["status"] == "needs_info"
    assert result["question"] == "Is this for an operator/agency quote or a direct client quote?"


def test_missing_year_asks_for_dates_before_pricing():
    result = prepare("Operator Manakin. Sep 4. 9 clients. Birding day trip. Breakfast and lunch.")

    assert result["status"] == "needs_info"
    assert "dates" in result["question"].lower()


def test_custom_event_quote_is_not_auto_priced_as_lodging():
    result = prepare(
        "Company COTELCO Caldas. Contact Wladimir. Nov 15-16 2026. "
        "13 people. Congreso de Aviturismo. Area rental plus morning refreshments both days."
    )

    assert result["status"] == "needs_info"
    assert "cabin stay or birding day trip" in result["question"]


def test_gmail_browser_url_extracts_search_clue():
    query, source = server.normalize_gmail_search_query(
        "https://mail.google.com/mail/u/2/#search/neptuno/FMfcgzQfCDMHsnfFpCCpKBmgvzGXPRhM"
    )

    assert source == "gmail_url_search"
    assert query == "neptuno"


def test_gmail_inbox_url_extracts_message_token():
    query, source = server.normalize_gmail_search_query(
        "https://mail.google.com/mail/u/2/#inbox/FMfcgzQfCDMHsnfFpCCpKBmgvzGXPRhM"
    )

    assert source == "gmail_url_message"
    assert query == "FMfcgzQfCDMHsnfFpCCpKBmgvzGXPRhM"


def test_gmail_url_search_mock_resolves_neptuno_thread():
    previous = os.environ.get("OWLSWATCH_QUOTES_MOCKS")
    os.environ["OWLSWATCH_QUOTES_MOCKS"] = "1"
    try:
        result = server.tool_gmail_search_quote_threads({
            "query": "https://mail.google.com/mail/u/2/#search/neptuno/FMfcgzQfCDMHsnfFpCCpKBmgvzGXPRhM",
            "maxResults": 5,
        })
    finally:
        if previous is None:
            os.environ.pop("OWLSWATCH_QUOTES_MOCKS", None)
        else:
            os.environ["OWLSWATCH_QUOTES_MOCKS"] = previous

    assert result["ok"] is True
    assert result["querySource"] == "gmail_url_search"
    assert result["query"] == "neptuno"
    assert result["matches"][0]["threadId"] == "mock-thread-neptuno-trachsel"


def test_gmail_inbox_url_mock_resolves_neptuno_thread():
    previous = os.environ.get("OWLSWATCH_QUOTES_MOCKS")
    os.environ["OWLSWATCH_QUOTES_MOCKS"] = "1"
    try:
        result = server.tool_gmail_search_quote_threads({
            "query": "https://mail.google.com/mail/u/2/#inbox/FMfcgzQfCDMHsnfFpCCpKBmgvzGXPRhM",
            "maxResults": 5,
        })
    finally:
        if previous is None:
            os.environ.pop("OWLSWATCH_QUOTES_MOCKS", None)
        else:
            os.environ["OWLSWATCH_QUOTES_MOCKS"] = previous

    assert result["ok"] is True
    assert result["querySource"] == "gmail_url_message"
    assert result["matches"][0]["threadId"] == "mock-thread-neptuno-trachsel"


def test_mock_gmail_thread_latest_reply_drives_prepared_dates():
    previous = os.environ.get("OWLSWATCH_QUOTES_MOCKS")
    os.environ["OWLSWATCH_QUOTES_MOCKS"] = "1"
    try:
        thread = server.tool_gmail_read_thread({"threadId": "mock-thread-neptuno-trachsel"})
        messages = thread["thread"]["messages"]
        assert "Dec 28 2026 - Jan 1 2027" in messages[0]["bodyText"]
        assert "accepted for Dec 28-31, 2026" in messages[-1]["bodyText"]

        result = server.tool_quote_prepare({
            "raw_text": (
                "Latest operative facts from Gmail thread mock-thread-neptuno-trachsel. "
                "Operator Promotora Neptuno / Dreamtime Travel AG. "
                "Client Trachsel x2. 2 guests. 1 cabin. "
                "Accepted dates Dec 28-31, 2026. Full board. "
                "No birding or transport mentioned."
            ),
            "source_metadata": {"source": "gmail", "threadId": "mock-thread-neptuno-trachsel"},
            "mode": "draft",
        })
    finally:
        if previous is None:
            os.environ.pop("OWLSWATCH_QUOTES_MOCKS", None)
        else:
            os.environ["OWLSWATCH_QUOTES_MOCKS"] = previous

    assert_ready(result)
    assert result["canonicalPayload"]["arrivalDate"] == "2026-12-28"
    assert result["canonicalPayload"]["departureDate"] == "2026-12-31"
    assert result["canonicalPayload"]["nights"] == 3
