import importlib.util
import datetime as dt
import pathlib
import tempfile
import unittest


SERVER_PATH = pathlib.Path(__file__).resolve().parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("hotel_pms_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(server)


class ReservationToolValidationTest(unittest.TestCase):
    def test_prepare_payload_rejects_raw_telegram_text(self):
        with self.assertRaises(server.ToolError) as ctx:
            server.validate_prepare_payload({"rawText": "crear reserva para Camilo"})
        self.assertEqual(ctx.exception.code, "invalid_input")

    def test_prepare_payload_accepts_cabin_and_guide_cabin(self):
        payload = server.validate_prepare_payload({
            "bookingType": "overnight_stay",
            "guestName": "Camilo Martinez",
            "arrivalDate": "2026-06-21",
            "departureDate": "2026-06-22",
            "adultsCount": 2,
            "unitAllocations": [
                {"unitCode": "cabin", "quantity": 1},
                {"unitCode": "guide-cabin", "quantity": 1},
            ],
            "source": "direct",
        })
        self.assertEqual(payload["unitAllocations"][0]["unitCode"], "cabin")
        self.assertEqual(payload["unitAllocations"][1]["unitCode"], "guide-cabin")

    def test_prepare_payload_rejects_legacy_guide_room_unit(self):
        with self.assertRaises(server.ToolError) as ctx:
            server.validate_prepare_payload({
                "bookingType": "overnight_stay",
                "unitAllocations": [{"unitCode": "guide-room", "quantity": 1}],
            })
        self.assertEqual(ctx.exception.code, "invalid_input")

    def test_prepare_payload_rejects_ota_sources(self):
        for source in ("booking_com", "expedia", "airbnb", "beds24"):
            with self.subTest(source=source):
                with self.assertRaises(server.ToolError):
                    server.validate_prepare_payload({"source": source})

    def test_staff_safe_value_redacts_tokens_hashes_and_finance(self):
        safe = server.staff_safe_value({
            "summary": "Reserva lista",
            "preparedToken": "secret-token",
            "payloadHash": "abc123",
            "total": 123,
            "nested": {"authorization": "Bearer nope", "guestName": "Camilo"},
        })
        self.assertEqual(safe, {"summary": "Reserva lista", "nested": {"guestName": "Camilo"}})

    def test_yes_confirmation_accepts_si_and_rejects_ok(self):
        self.assertEqual(server.validate_yes_confirmation("sí"), "si")
        self.assertEqual(server.validate_yes_confirmation("si"), "si")
        with self.assertRaises(server.ToolError) as ctx:
            server.validate_yes_confirmation("ok")
        self.assertEqual(ctx.exception.code, "confirmation_required")

    def test_ambiguous_single_guest_guard_asks_for_count(self):
        payload = server.validate_prepare_payload({
            "bookingType": "overnight_stay",
            "guestName": "Bailey",
            "arrivalDate": "2027-07-05",
            "departureDate": "2027-07-07",
            "adultsCount": 1,
            "unitAllocations": [{"unitCode": "cabin", "quantity": 1}],
        })
        guard = server.ambiguous_single_guest_guard(
            {"sourceText": "Reservar cabaña para familia Bailey del 5 al 7 de julio 2027."},
            payload,
        )
        self.assertIsNotNone(guard)
        self.assertEqual(guard["status"], "needs_info")
        self.assertIn("personas", guard["question"])

    def test_missing_guest_count_guard_asks_for_count(self):
        payload = server.validate_prepare_payload({
            "bookingType": "overnight_stay",
            "guestName": "Bailey",
            "arrivalDate": "2027-07-05",
            "departureDate": "2027-07-07",
            "unitAllocations": [{"unitCode": "cabin", "quantity": 1}],
        })
        guard = server.ambiguous_single_guest_guard(
            {"sourceText": "Reservar cabaña para familia Bailey del 5 al 7 de julio 2027."},
            payload,
        )
        self.assertIsNotNone(guard)
        self.assertEqual(guard["reason"], "guest_count_missing")

    def test_ambiguous_single_guest_guard_allows_explicit_one_person(self):
        payload = server.validate_prepare_payload({
            "bookingType": "overnight_stay",
            "guestName": "Bailey",
            "arrivalDate": "2027-07-05",
            "departureDate": "2027-07-07",
            "adultsCount": 1,
            "unitAllocations": [{"unitCode": "cabin", "quantity": 1}],
        })
        guard = server.ambiguous_single_guest_guard(
            {"sourceText": "Reservar cabaña para Bailey, 1 persona, del 5 al 7 de julio 2027."},
            payload,
        )
        self.assertIsNone(guard)

    def test_date_year_guard_asks_when_source_has_month_without_year(self):
        payload = server.validate_prepare_payload({
            "bookingType": "overnight_stay",
            "guestName": "Prueba Dennis 2",
            "arrivalDate": "2027-10-02",
            "departureDate": "2027-10-03",
            "adultsCount": 2,
            "unitAllocations": [{"unitCode": "cabin", "quantity": 1}],
        })
        guard = server.source_text_date_year_guard(
            {"sourceText": "Crear reserva para Prueba Dennis 2, 2 personas, cabaña, 2-3 octubre."},
            payload,
        )
        self.assertIsNotNone(guard)
        self.assertEqual(guard["reason"], "date_year_missing")
        self.assertIn("año", guard["question"])

    def test_date_year_guard_allows_explicit_year(self):
        payload = server.validate_prepare_payload({
            "bookingType": "overnight_stay",
            "guestName": "Prueba Dennis 2",
            "arrivalDate": "2027-10-02",
            "departureDate": "2027-10-03",
            "adultsCount": 2,
            "unitAllocations": [{"unitCode": "cabin", "quantity": 1}],
        })
        guard = server.source_text_date_year_guard(
            {"sourceText": "Crear reserva para Prueba Dennis 2, 2 personas, cabaña, 2-3 octubre 2027."},
            payload,
        )
        self.assertIsNone(guard)

    def test_date_year_guard_allows_relative_date(self):
        payload = server.validate_prepare_payload({
            "bookingType": "bird_tour",
            "guestName": "Catherine",
            "visitDate": "2026-06-26",
            "adultsCount": 2,
        })
        guard = server.source_text_date_year_guard(
            {"sourceText": "Crear tour de aves mañana para Catherine, 2 personas."},
            payload,
        )
        self.assertIsNone(guard)

    def test_store_and_load_pending_draft_hides_code_from_public_result(self):
        old_dir = server.RESERVATION_DRAFT_DIR
        with tempfile.TemporaryDirectory() as tmp:
            server.RESERVATION_DRAFT_DIR = pathlib.Path(tmp)
            try:
                pending_id = server.store_reservation_draft(
                    {
                        "confirmationCode": "A7K2",
                        "preparedToken": "prepared-secret",
                        "expiresAt": (server.now_utc() + server.dt.timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                        "summary": {"guestName": "Camilo"},
                    },
                    {"guestName": "Camilo"},
                )
                self.assertRegex(pending_id, r"^[A-F0-9]{16}$")
                pending = server.load_reservation_draft(pending_id, by_pending_id=True)
                legacy = server.load_reservation_draft("A7K2")
                self.assertEqual(pending["confirmationCode"], "A7K2")
                self.assertEqual(legacy["pendingId"], pending_id)
            finally:
                server.RESERVATION_DRAFT_DIR = old_dir

    def test_store_pending_draft_preserves_source_metadata_for_create(self):
        old_dir = server.RESERVATION_DRAFT_DIR
        with tempfile.TemporaryDirectory() as tmp:
            server.RESERVATION_DRAFT_DIR = pathlib.Path(tmp)
            try:
                pending_id = server.store_reservation_draft(
                    {
                        "confirmationCode": "B8R4",
                        "preparedToken": "prepared-secret",
                        "expiresAt": (server.now_utc() + server.dt.timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
                        "summary": {"guestName": "Prueba Dennis"},
                    },
                    {
                        "guestName": "Prueba Dennis",
                        "sourceMetadata": {
                            "source": "telegram",
                            "telegramChatId": "-5588592355",
                            "telegramUserId": "6831734977",
                            "telegramMessageId": "59",
                            "telegramDisplayName": "Steady Dee",
                        },
                    },
                )
                pending = server.load_reservation_draft(pending_id, by_pending_id=True)
                self.assertEqual(pending["sourceMetadata"]["telegramChatId"], "-5588592355")
                self.assertEqual(pending["sourceMetadata"]["telegramUserId"], "6831734977")
                self.assertEqual(pending["sourceMetadata"]["telegramMessageId"], "59")
            finally:
                server.RESERVATION_DRAFT_DIR = old_dir

    def test_merge_source_metadata_lets_confirmation_message_override_message_id(self):
        merged = server.merge_source_metadata(
            {
                "source": "telegram",
                "telegramChatId": "-5588592355",
                "telegramUserId": "6831734977",
                "telegramMessageId": "59",
            },
            {
                "source": "telegram",
                "telegramMessageId": "60",
                "telegramDisplayName": "Steady Dee",
            },
        )
        self.assertEqual(merged["telegramChatId"], "-5588592355")
        self.assertEqual(merged["telegramUserId"], "6831734977")
        self.assertEqual(merged["telegramMessageId"], "60")
        self.assertEqual(merged["telegramDisplayName"], "Steady Dee")

    def test_booking_category_uses_day_pass_not_nights(self):
        reservation = {
            "guestName": "Sergio Henao",
            "bookingType": "day_pass",
            "nights": 1,
            "unitType": None,
        }
        self.assertEqual(server.booking_category(reservation), "day_pass")
        self.assertEqual(server.visit_phrase(reservation), "pasadía")

    def test_booking_category_uses_bird_tour(self):
        reservation = {
            "guestName": "Catherine Gordon",
            "bookingType": "bird_tour",
            "nights": 0,
            "unitType": None,
        }
        self.assertEqual(server.booking_category(reservation), "bird_tour")
        self.assertEqual(server.visit_phrase(reservation), "tour de aves")

    def test_booking_category_does_not_guess_cabin_from_nights_without_type(self):
        reservation = {
            "guestName": "Sergio Henao",
            "bookingType": None,
            "nights": 1,
            "unitType": None,
        }
        self.assertEqual(server.booking_category(reservation), "unknown")
        self.assertEqual(server.visit_phrase(reservation), "reserva sin tipo definido")

    def test_booking_category_does_not_guess_lodging_when_not_overnight(self):
        reservation = {
            "guestName": "Keith",
            "bookingType": None,
            "isOvernight": False,
            "nights": 1,
            "unitType": "Cabin",
            "departureDate": "2026-07-06",
        }
        self.assertEqual(server.booking_category(reservation), "unknown")
        self.assertEqual(server.visit_phrase(reservation), "reserva sin tipo definido")

    def test_normalize_reservation_uses_booking_type_from_list_row(self):
        normalized = server.normalize_reservation(
            {
                "reservationId": "res-123",
                "guestName": "Sergio Henao",
                "bookingType": "day_pass",
                "isOvernight": False,
                "visitDate": "2026-06-27",
                "departureDate": "2026-06-28",
                "nights": 1,
            },
            {"reservation": {"reservationId": "res-123", "guestName": "Sergio Henao"}},
            {},
            "arrival",
        )
        self.assertEqual(normalized["bookingCategory"], "day_pass")
        self.assertEqual(normalized["visitPhrase"], "pasadía")
        self.assertEqual(normalized["isOvernight"], False)
        self.assertEqual(normalized["visitDate"], "2026-06-27")
        self.assertIsNone(normalized["departureDate"])
        self.assertEqual(normalized["nights"], 0)

    def test_same_day_activities_are_not_lodging_movements(self):
        rows = [
            {"reservationId": "day-pass", "bookingType": "day_pass", "guestName": "Sergio"},
            {"reservationId": "bird-tour", "bookingType": "bird_tour", "guestName": "Poorvi"},
            {"reservationId": "not-overnight", "bookingType": None, "isOvernight": False, "guestName": "Keith", "unitType": "Cabin"},
            {
                "reservationId": "cabin",
                "bookingType": "overnight_stay",
                "guestName": "Camilo",
                "unitAllocations": [{"unitCode": "cabin", "unitName": "Forest Cabin"}],
            },
            {"reservationId": "unknown", "bookingType": None, "guestName": "Manual row"},
        ]
        filtered = server.filter_lodging_movements(rows)
        self.assertEqual([row["reservationId"] for row in filtered], ["cabin", "unknown"])

    def test_safe_reservation_summary_keeps_operational_booking_fields(self):
        row = {
            "reservationId": "activity",
            "guestName": "Keith",
            "bookingType": "bird_tour",
            "isOvernight": False,
            "visitDate": "2026-07-05",
            "departureDate": None,
            "nights": 0,
            "totalAmount": 123456,
        }
        summary = server.safe_reservation_summary(row)
        self.assertEqual(summary["bookingType"], "bird_tour")
        self.assertEqual(summary["isOvernight"], False)
        self.assertEqual(summary["visitDate"], "2026-07-05")
        self.assertNotIn("totalAmount", summary)

    def test_booking_category_uses_cabin_unit_for_legacy_rows(self):
        reservation = {
            "guestName": "Bailey",
            "bookingType": None,
            "nights": 1,
            "unitType": "Cabin",
        }
        self.assertEqual(server.booking_category(reservation), "cabin")
        self.assertEqual(server.visit_phrase(reservation), "cabañas")

    def test_booking_category_uses_unit_allocations_from_pms(self):
        reservation = {
            "guestName": "Wild About Colombia",
            "bookingType": "overnight_stay",
            "unitAllocations": [
                {"unitCode": "cabin", "unitName": "Forest Cabin"},
                {"unitCode": "guide-cabin", "unitName": "Guide Cabin"},
            ],
        }
        self.assertEqual(server.booking_category(reservation), "cabin")
        self.assertEqual(server.visit_phrase(reservation), "cabañas")

    def test_booking_category_handles_guide_cabin_only(self):
        reservation = {
            "guestName": "Guide",
            "bookingType": "overnight_stay",
            "unitAllocations": [{"unitCode": "guide-cabin", "unitName": "Guide Cabin"}],
        }
        self.assertEqual(server.booking_category(reservation), "guide_room")
        self.assertEqual(server.visit_phrase(reservation), "habitación de guía")

    def test_scrub_registro_metadata_removes_fetch_tokens_and_bytes(self):
        scrubbed = server.scrub_registro_metadata({
            "documentId": "doc-1",
            "fileName": "passport.jpg",
            "fetchToken": "secret",
            "fetchUrl": "https://example.test/secret",
            "fileBase64": "abc",
            "sizeBytes": 1234,
            "nested": {"token": "nested-secret", "contentType": "image/jpeg"},
        })
        self.assertEqual(scrubbed["documentId"], "doc-1")
        self.assertEqual(scrubbed["fileName"], "passport.jpg")
        self.assertEqual(scrubbed["sizeBytes"], 1234)
        self.assertEqual(scrubbed["nested"], {"contentType": "image/jpeg"})
        self.assertNotIn("fetchToken", scrubbed)
        self.assertNotIn("fetchUrl", scrubbed)
        self.assertNotIn("fileBase64", scrubbed)

    def test_guests_from_registro_response_supports_new_and_legacy_shapes(self):
        new_shape = {"guests": [{"registrationGuestId": "guest-1"}, {"registrationGuestId": "guest-2"}]}
        self.assertEqual(len(server.guests_from_registro_response(new_shape, "reg-1")), 2)
        legacy = {"registrationId": "reg-1", "guestName": "Rishab", "documentNumber": "P123"}
        guests = server.guests_from_registro_response(legacy, "reg-1")
        self.assertEqual(guests[0]["registrationGuestId"], "reg-1")
        self.assertEqual(guests[0]["displayName"], "Rishab")

    def test_normalize_registro_extraction_maps_passport_fields(self):
        extracted = server.normalize_registro_extraction({
            "documentType": "passport",
            "documentNumber": "P123456",
            "nationalityIso": "IND",
            "nationalityLabel": "India",
            "surname": "KUMAR SHARMA",
            "givenNames": "RISHAB",
            "dateOfBirth": "1990-02-03",
            "sex": "M",
            "documentExpirationDate": "2030-01-01",
            "mrzChecksumsOk": True,
            "confidence": 91,
            "flags": [],
            "rawVisibleText": "visible",
        })
        self.assertEqual(extracted["docType"], "passport")
        self.assertEqual(extracted["docNumber"], "P123456")
        self.assertEqual(extracted["nationalityIso"], "IND")
        self.assertEqual(extracted["primerApellido"], "KUMAR")
        self.assertEqual(extracted["segundoApellido"], "SHARMA")
        self.assertEqual(extracted["nombres"], "RISHAB")
        self.assertEqual(extracted["fechaNacimiento"], "1990-02-03")
        self.assertTrue(extracted["sireRequired"])
        self.assertEqual(extracted["validationErrors"], [])

    def test_parse_passport_mrz_extracts_document_number_and_dates(self):
        parsed = server.parse_passport_mrz(
            "P<USAGHOSH<<RISHAB<AIYER<<<<<<<<<<<<<<<<<<\n"
            "A366987746USA7509074M2806239<<<<<<<<<<<<<<08"
        )
        self.assertEqual(parsed["docNumber"], "A36698774")
        self.assertEqual(parsed["nationalityIso"], "USA")
        self.assertEqual(parsed["primerApellido"], "GHOSH")
        self.assertEqual(parsed["nombres"], "RISHAB AIYER")
        self.assertEqual(parsed["fechaNacimiento"], "1975-09-07")
        self.assertEqual(parsed["docExpiry"], "2028-06-23")
        self.assertTrue(parsed["mrzChecksumsOk"])

    def test_normalize_registro_extraction_prefers_mrz_document_number(self):
        extracted = server.normalize_registro_extraction({
            "documentType": "passport",
            "documentNumber": "8407269",
            "nationalityIso": "USA",
            "nationalityLabel": "USA",
            "surname": "SLUTU",
            "givenNames": "VIRGINIA",
            "dateOfBirth": "1984-07-26",
            "sex": "F",
            "documentExpirationDate": "2032-08-10",
            "mrzChecksumsOk": None,
            "confidence": 95,
            "flags": [],
            "rawVisibleText": (
                "P<USASLUTU<<VIRGINIA<<<<<<<<<<<<<<<<<<<<<<\n"
                "A061328996USA8407269F3208106<<<<<<<<<<<<<<06"
            ),
        })
        self.assertEqual(extracted["docNumber"], "A06132899")
        self.assertEqual(extracted["fechaNacimiento"], "1984-07-26")
        self.assertEqual(extracted["docExpiry"], "2032-08-10")

    def test_parse_passport_mrz_accepts_line2_without_fillers(self):
        parsed = server.parse_passport_mrz("A061328996USA8407269F3208106573627802")
        self.assertEqual(parsed["docNumber"], "A06132899")
        self.assertEqual(parsed["fechaNacimiento"], "1984-07-26")
        self.assertEqual(parsed["docExpiry"], "2032-08-10")

    def test_usable_mrz_name_rejects_filler_noise(self):
        self.assertIsNone(server.usable_mrz_name("SLUTUS VIRGINIASERERRRRRRRRRERERER"))
        self.assertEqual(server.usable_mrz_name("SLUTU"), "SLUTU")

    def test_normalize_registro_extraction_flags_missing_required_fields(self):
        extracted = server.normalize_registro_extraction({
            "documentType": None,
            "documentNumber": None,
            "nationalityIso": None,
            "nationalityLabel": None,
            "surname": None,
            "givenNames": None,
            "dateOfBirth": None,
            "confidence": 0.4,
            "flags": ["image_blurry"],
        })
        self.assertIn("document_number_missing", extracted["validationErrors"])
        self.assertIn("birth_date_missing", extracted["validationErrors"])
        self.assertIn("low_confidence", extracted["validationErrors"])

    def test_normalize_registro_extraction_flags_failed_mrz_checksum(self):
        extracted = server.normalize_registro_extraction({
            "documentType": "passport",
            "documentNumber": "P123456",
            "nationalityIso": "USA",
            "nationalityLabel": "United States",
            "surname": "BAILEY",
            "givenNames": "DENNIS",
            "dateOfBirth": "1970-01-01",
            "sex": "M",
            "documentExpirationDate": "2030-01-01",
            "mrzChecksumsOk": False,
            "confidence": 95,
            "flags": [],
        })
        self.assertIn("mrz_checksum_failed", extracted["validationErrors"])

    def test_registro_submission_plan_ready_uses_due_types_without_pii(self):
        plan = server.build_registro_submission_plan(
            {
                "registrationId": "reg-1",
                "status": "validated",
                "documentCount": 2,
                "dueSubmissionTypes": ["tra", "sire_entrada"],
            },
            [
                {
                    "role": "primary",
                    "displayName": "Sensitive Name",
                    "documentNumber": "A12345678",
                    "submissionStatus": "ready",
                    "extractionStatus": "extracted",
                    "missingFields": [],
                },
                {
                    "role": "companion",
                    "displayName": "Sensitive Companion",
                    "documentNumber": "B12345678",
                    "submissionStatus": "ready",
                    "extractionStatus": "extracted",
                    "missingFields": [],
                },
            ],
        )
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["guestCount"], 2)
        self.assertEqual(plan["readyGuestCount"], 2)
        self.assertEqual([item["submissionType"] for item in plan["stagedSubmissions"]], ["tra", "sire_entrada"])
        rendered = str(plan)
        self.assertNotIn("A12345678", rendered)
        self.assertNotIn("Sensitive Name", rendered)

    def test_registro_submission_plan_blocks_incomplete_guest(self):
        plan = server.build_registro_submission_plan(
            {"registrationId": "reg-1", "status": "validated", "dueSubmissionTypes": ["tra"]},
            [
                {
                    "role": "primary",
                    "submissionStatus": "needs_info",
                    "extractionStatus": "needs_review",
                    "missingFields": ["birthDate"],
                }
            ],
        )
        self.assertEqual(plan["status"], "needs_info")
        self.assertEqual(plan["readyGuestCount"], 0)
        self.assertEqual(plan["blockers"][0]["scope"], "guest_primary")
        self.assertIn("missing_fields", plan["blockers"][0]["reasons"])

    def test_registro_submission_plan_trusts_pms_ready_status(self):
        plan = server.build_registro_submission_plan(
            {"registrationId": "reg-1", "status": "validated", "dueSubmissionTypes": ["tra"]},
            [
                {
                    "role": "primary",
                    "submissionStatus": "ready",
                    "extractionStatus": "needs_review",
                    "missingFields": [],
                }
            ],
        )
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["readyGuestCount"], 1)

    def test_registro_submission_plan_no_due_is_no_due_when_complete(self):
        plan = server.build_registro_submission_plan(
            {"registrationId": "reg-1", "status": "complete", "dueSubmissionTypes": []},
            [{"role": "primary", "submissionStatus": "ready", "missingFields": []}],
        )
        self.assertEqual(plan["status"], "no_due")

    def test_sire_document_type_maps_generic_id_to_foreign_document(self):
        self.assertEqual(server.sire_document_type("id_card"), "Documento Extranjero")
        self.assertEqual(server.sire_document_type("passport"), "Pasaporte")
        self.assertEqual(server.sire_document_type("cedula de ciudadania"), "Cedula de Ciudadania")

    def test_submission_type_normalization_and_validation(self):
        self.assertEqual(server.normalize_submission_type("SIRE"), "sire_entrada")
        self.assertEqual(server.normalize_submission_type("sire-salida"), "sire_salida")
        with self.assertRaises(server.ToolError):
            server.normalize_submission_type("dian")

    def test_submission_status_tool_rejects_submitted_state(self):
        with self.assertRaises(server.ToolError) as ctx:
            server.validate_submission_state("submitted")
        self.assertEqual(ctx.exception.code, "live_submission_not_enabled")
        self.assertEqual(server.validate_submission_state("pending"), "pending")

    def test_safe_government_submission_summary_omits_payload_identity_fields(self):
        summary = server.safe_government_submission_summary({
            "registrationId": "reg-1",
            "reservationId": "res-1",
            "submissionType": "tra",
            "status": "ready",
            "idempotencyKey": "registro:reg-1:tra:government-v1",
            "receiptGranularity": "registration",
            "dueSubmissionTypes": ["tra", "sire_entrada"],
            "payload": {
                "reservation": {
                    "arrivalDate": "2026-06-26",
                    "departureDate": "2026-07-01",
                },
                "guests": [
                    {
                        "documentNumber": "A12345678",
                        "birthDate": "1975-09-07",
                        "firstName": "Sensitive",
                    }
                ],
            },
        })
        self.assertEqual(summary["status"], "ready")
        self.assertEqual(summary["guestCount"], 1)
        rendered = str(summary)
        self.assertNotIn("payload", rendered)
        self.assertNotIn("A12345678", rendered)
        self.assertNotIn("Sensitive", rendered)
        self.assertNotIn("1975-09-07", rendered)

    def test_submit_mode_validation(self):
        self.assertEqual(server.validate_submit_mode(None), "dry_run")
        self.assertEqual(server.validate_submit_mode("submit"), "submit")
        with self.assertRaises(server.ToolError):
            server.validate_submit_mode("send")

    def test_receipt_reference_from_response_supports_common_shapes(self):
        self.assertEqual(server.receipt_reference_from_response({"radicado": "RAD-123"}), "RAD-123")
        self.assertEqual(server.receipt_reference_from_response({"data": {"numeroRadicado": "NR-456"}}), "NR-456")
        self.assertIsNone(server.receipt_reference_from_response({"ok": True}))

    def test_government_submitter_dry_run_omits_payload_identity_fields(self):
        calls: list[tuple[str, dict, str]] = []

        def fake_pms_tool(config, tool_name, input_payload=None, profile="read"):
            calls.append((tool_name, input_payload or {}, profile))
            if tool_name == "registro_get_by_reservation":
                return {"registrationId": "reg-1", "status": "validated", "dueSubmissionTypes": ["tra"]}
            if tool_name == "registro_list_guests":
                return {"guests": [{"registrationGuestId": "guest-1", "submissionStatus": "ready", "extractionStatus": "extracted", "missingFields": []}]}
            if tool_name == "registro_prepare_government_submission":
                return {
                    "registrationId": "reg-1",
                    "reservationId": "res-1",
                    "submissionType": input_payload["submissionType"],
                    "status": "ready",
                    "idempotencyKey": "registro:reg-1:tra",
                    "payload": {
                        "guests": [{"documentNumber": "A12345678", "birthDate": "1975-09-07", "firstName": "Sensitive"}],
                        "reservation": {"arrivalDate": "2026-06-26"},
                    },
                }
            raise AssertionError(f"unexpected PMS tool {tool_name}")

        old_load = server.load_config
        old_pms = server.pms_tool
        try:
            server.load_config = lambda: {}
            server.pms_tool = fake_pms_tool
            result = server.tool_hotel_registro_submit_government({"reservationId": "res-1", "mode": "dry_run"})
        finally:
            server.load_config = old_load
            server.pms_tool = old_pms

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["mode"], "dry_run")
        self.assertEqual(result["results"][0]["guestCount"], 1)
        rendered = str(result)
        self.assertNotIn("A12345678", rendered)
        self.assertNotIn("1975-09-07", rendered)
        self.assertNotIn("Sensitive", rendered)
        self.assertFalse(any(call[0] == "registro_record_submission" for call in calls))

    def test_government_submitter_submit_disabled_does_not_record(self):
        calls: list[tuple[str, dict, str]] = []

        def fake_pms_tool(config, tool_name, input_payload=None, profile="read"):
            calls.append((tool_name, input_payload or {}, profile))
            if tool_name == "registro_get_by_reservation":
                return {"registrationId": "reg-1", "status": "validated", "dueSubmissionTypes": ["tra"]}
            if tool_name == "registro_list_guests":
                return {"guests": [{"registrationGuestId": "guest-1", "submissionStatus": "ready", "extractionStatus": "extracted", "missingFields": []}]}
            if tool_name == "registro_prepare_government_submission":
                return {
                    "registrationId": "reg-1",
                    "submissionType": "tra",
                    "status": "ready",
                    "payload": {"guests": [{"documentNumber": "A12345678"}]},
                }
            raise AssertionError(f"unexpected PMS tool {tool_name}")

        old_load = server.load_config
        old_pms = server.pms_tool
        old_enabled = server.government_submitter_enabled
        try:
            server.load_config = lambda: {}
            server.pms_tool = fake_pms_tool
            server.government_submitter_enabled = lambda config: False
            result = server.tool_hotel_registro_submit_government({"reservationId": "res-1", "mode": "submit"})
        finally:
            server.load_config = old_load
            server.pms_tool = old_pms
            server.government_submitter_enabled = old_enabled

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["results"][0]["reason"], "government_submitter_disabled")
        self.assertFalse(any(call[0] == "registro_record_submission" for call in calls))

    def test_government_submitter_tra_success_records_submitted_only_with_receipt(self):
        recorded: list[dict] = []

        def fake_pms_tool(config, tool_name, input_payload=None, profile="read"):
            if tool_name == "registro_get_by_reservation":
                return {"registrationId": "reg-1", "status": "validated", "dueSubmissionTypes": ["tra"]}
            if tool_name == "registro_list_guests":
                return {"guests": [{"registrationGuestId": "guest-1", "submissionStatus": "ready", "extractionStatus": "extracted", "missingFields": []}]}
            if tool_name == "registro_prepare_government_submission":
                return {
                    "registrationId": "reg-1",
                    "reservationId": "res-1",
                    "submissionType": "tra",
                    "status": "ready",
                    "idempotencyKey": "registro:reg-1:tra",
                    "payload": {"guests": [{"documentNumber": "A12345678"}]},
                }
            if tool_name == "registro_record_submission":
                recorded.append(input_payload or {})
                return {"state": input_payload["state"], "receiptReference": input_payload["receiptReference"]}
            raise AssertionError(f"unexpected PMS tool {tool_name}")

        old_load = server.load_config
        old_pms = server.pms_tool
        old_enabled = server.government_submitter_enabled
        old_tra = server.call_tra_submitter
        try:
            server.load_config = lambda: {}
            server.pms_tool = fake_pms_tool
            server.government_submitter_enabled = lambda config: True
            server.call_tra_submitter = lambda config, prepared: {
                "ok": True,
                "status": "submitted",
                "receiptReference": "TRA-RECEIPT-1",
                "responseSummary": {"radicado": "TRA-RECEIPT-1"},
            }
            result = server.tool_hotel_registro_submit_government({"reservationId": "res-1", "mode": "submit"})
        finally:
            server.load_config = old_load
            server.pms_tool = old_pms
            server.government_submitter_enabled = old_enabled
            server.call_tra_submitter = old_tra

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["results"][0]["receiptReference"], "TRA-RECEIPT-1")
        self.assertEqual(recorded[0]["state"], "submitted")
        self.assertEqual(recorded[0]["receiptReference"], "TRA-RECEIPT-1")
        self.assertEqual(recorded[0]["idempotencyKey"], "registro:reg-1:tra")

    def test_government_submitter_tra_missing_receipt_records_failed_not_submitted(self):
        recorded: list[dict] = []

        def fake_pms_tool(config, tool_name, input_payload=None, profile="read"):
            if tool_name == "registro_get_by_reservation":
                return {"registrationId": "reg-1", "status": "validated", "dueSubmissionTypes": ["tra"]}
            if tool_name == "registro_list_guests":
                return {"guests": [{"registrationGuestId": "guest-1", "submissionStatus": "ready", "extractionStatus": "extracted", "missingFields": []}]}
            if tool_name == "registro_prepare_government_submission":
                return {
                    "registrationId": "reg-1",
                    "submissionType": "tra",
                    "status": "ready",
                    "idempotencyKey": "registro:reg-1:tra",
                    "payload": {"guests": [{"documentNumber": "A12345678"}]},
                }
            if tool_name == "registro_record_submission":
                recorded.append(input_payload or {})
                return {"state": input_payload["state"]}
            raise AssertionError(f"unexpected PMS tool {tool_name}")

        old_load = server.load_config
        old_pms = server.pms_tool
        old_enabled = server.government_submitter_enabled
        old_tra = server.call_tra_submitter
        try:
            server.load_config = lambda: {}
            server.pms_tool = fake_pms_tool
            server.government_submitter_enabled = lambda config: True
            server.call_tra_submitter = lambda config, prepared: {
                "ok": False,
                "status": "failed",
                "reason": "tra_response_missing_receipt",
                "responseSummary": {"ok": True},
            }
            result = server.tool_hotel_registro_submit_government({"reservationId": "res-1", "mode": "submit"})
        finally:
            server.load_config = old_load
            server.pms_tool = old_pms
            server.government_submitter_enabled = old_enabled
            server.call_tra_submitter = old_tra

        self.assertEqual(result["status"], "partial_failure")
        self.assertEqual(result["results"][0]["submitStatus"], "failed")
        self.assertEqual(recorded[0]["state"], "failed")
        self.assertNotIn("receiptReference", recorded[0])

    def test_government_submitter_sire_is_blocked_without_adapter(self):
        def fake_pms_tool(config, tool_name, input_payload=None, profile="read"):
            if tool_name == "registro_get_by_reservation":
                return {"registrationId": "reg-1", "status": "validated", "dueSubmissionTypes": ["sire_entrada"]}
            if tool_name == "registro_list_guests":
                return {"guests": [{"registrationGuestId": "guest-1", "submissionStatus": "ready", "extractionStatus": "extracted", "missingFields": []}]}
            if tool_name == "registro_prepare_government_submission":
                return {
                    "registrationId": "reg-1",
                    "submissionType": "sire_entrada",
                    "status": "ready",
                    "payload": {
                        "reservation": {"arrivalDate": "2026-06-26", "departureDate": "2026-06-27"},
                        "guests": [{
                            "documentType": "passport",
                            "documentNumber": "A12345678",
                            "birthDate": "1975-09-07",
                            "firstName": "Sensitive",
                            "lastName": "Guest",
                            "nationalityCountry": "United States",
                            "originCountry": "United States",
                            "destinationCountry": "Colombia",
                            "sireRequired": True,
                        }],
                    },
                }
            raise AssertionError(f"unexpected PMS tool {tool_name}")

        old_load = server.load_config
        old_pms = server.pms_tool
        old_enabled = server.government_submitter_enabled
        try:
            server.load_config = lambda: {}
            server.pms_tool = fake_pms_tool
            server.government_submitter_enabled = lambda config: True
            result = server.tool_hotel_registro_submit_government({"reservationId": "res-1", "mode": "submit"})
        finally:
            server.load_config = old_load
            server.pms_tool = old_pms
            server.government_submitter_enabled = old_enabled

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["results"][0]["reason"], "sire_submitter_not_configured")
        self.assertEqual(result["results"][0]["payloadSummary"]["recordCount"], 1)
        rendered = str(result)
        self.assertNotIn("A12345678", rendered)
        self.assertNotIn("Sensitive", rendered)

    def test_build_sire_lodging_payload_uses_arrival_or_departure_date(self):
        prepared = {
            "registrationId": "reg-1",
            "reservationId": "res-1",
            "payload": {
                "property": {"propertyId": "owlswatch"},
                "reservation": {"arrivalDate": "2026-06-26", "departureDate": "2026-06-27"},
                "guests": [
                    {
                        "documentType": "passport",
                        "documentNumber": "A12345678",
                        "birthDate": "1975-09-07",
                        "firstName": "Miranda",
                        "lastName": "Davies Smith",
                        "nationalityCountry": "United States",
                        "originCountry": "United States",
                        "destinationCountry": "Colombia",
                        "sireRequired": True,
                    },
                    {
                        "documentType": "passport",
                        "documentNumber": "COL-SKIP",
                        "birthDate": "1975-09-07",
                        "firstName": "Skip",
                        "lastName": "Guest",
                        "nationalityCountry": "Colombia",
                        "originCountry": "Colombia",
                        "destinationCountry": "Colombia",
                        "sireRequired": False,
                    },
                ],
            },
        }
        entrada, entrada_result = server.build_sire_lodging_payload({}, prepared, "sire_entrada")
        salida, salida_result = server.build_sire_lodging_payload({}, prepared, "sire_salida")
        self.assertTrue(entrada_result["ok"])
        self.assertEqual(entrada["movementType"], "entrada")
        self.assertEqual(entrada["movementDate"], "2026-06-26")
        self.assertEqual(entrada["records"][0]["primerApellido"], "Davies")
        self.assertEqual(entrada["records"][0]["segundoApellido"], "Smith")
        self.assertEqual(len(entrada["records"]), 1)
        self.assertTrue(salida_result["ok"])
        self.assertEqual(salida["movementType"], "salida")
        self.assertEqual(salida["movementDate"], "2026-06-27")

    def test_build_sire_lodging_form_fields_maps_jsf_values(self):
        page_html = """
        <form id="cargueFormHospedaje">
          <input name="javax.faces.ViewState" value="vs-123">
          <input id="cargueFormHospedaje:fechaMovimientoInputCurrentDate" name="cargueFormHospedaje:fechaMovimientoInputCurrentDate" value="07/2026">
          <input id="cargueFormHospedaje:fechaNacimientoInputCurrentDate" name="cargueFormHospedaje:fechaNacimientoInputCurrentDate" value="09/1975">
          <select id="cargueFormHospedaje:tipoDocumento">
            <option value="">Seleccionar</option>
            <option value="3">PASAPORTE</option>
            <option value="5">CÉDULA DE EXTRANJERÍA</option>
          </select>
          <select id="cargueFormHospedaje:nacionalidad">
            <option value="">Seleccionar</option>
            <option value="249">ESTADOS UNIDOS DE AMERICA</option>
            <option value="169">COLOMBIA</option>
          </select>
          <select id="cargueFormHospedaje:procedencia">
            <option value="">Seleccionar</option>
            <option value="249">ESTADOS UNIDOS DE AMERICA</option>
            <option value="169">COLOMBIA</option>
          </select>
          <select id="cargueFormHospedaje:destino">
            <option value="">Seleccionar</option>
            <option value="249">ESTADOS UNIDOS DE AMERICA</option>
            <option value="169">COLOMBIA</option>
          </select>
        </form>
        """
        record = {
            "tipoMovimiento": "entrada",
            "fechaMovimiento": "2026-06-26",
            "tipoDocumento": "Pasaporte",
            "numeroDocumento": "A12345678",
            "fechaNacimiento": "1975-09-07",
            "primerApellido": "Davies",
            "segundoApellido": "Smith",
            "nombres": "Miranda",
            "nacionalidad": "United States",
            "paisProcedencia": "United States",
            "paisProximoDestino": "Colombia",
        }
        fields, result = server.build_sire_lodging_form_fields({}, {"records": [record]}, record, page_html)
        self.assertTrue(result["ok"])
        self.assertIn(("cargueFormHospedaje:tipoMovimiento", "3"), fields)
        self.assertIn(("cargueFormHospedaje:fechaMovimientoInputDate", "26/06/2026"), fields)
        self.assertIn(("cargueFormHospedaje:tipoDocumento", "3"), fields)
        self.assertIn(("cargueFormHospedaje:nacionalidad", "249"), fields)
        self.assertIn(("cargueFormHospedaje:procedencia", "249"), fields)
        self.assertIn(("cargueFormHospedaje:destino", "169"), fields)
        self.assertIn(("cargueFormHospedaje:j_id877", "cargueFormHospedaje:j_id877"), fields)

    def test_sire_verified_success_uses_valid_record_counts(self):
        page_html = """
        <span>Total Registros procesados: 2</span>
        <span>Num. Registros válidos: 2</span>
        <span>Num. Registros inválidos: 0</span>
        """
        ok, counts = server.sire_verified_success(page_html, 2)
        self.assertTrue(ok)
        self.assertEqual(counts["valid"], 2)
        self.assertEqual(counts["invalid"], 0)

    def test_call_sire_submitter_uses_jsf_when_credentials_are_configured(self):
        prepared = {
            "registrationId": "reg-1",
            "reservationId": "res-1",
            "payload": {
                "reservation": {"arrivalDate": "2026-06-26", "departureDate": "2026-06-27"},
                "guests": [{
                    "documentType": "passport",
                    "documentNumber": "A12345678",
                    "birthDate": "1975-09-07",
                    "firstName": "Sensitive",
                    "lastName": "Guest",
                    "nationalityCountry": "United States",
                    "originCountry": "United States",
                    "destinationCountry": "Colombia",
                    "sireRequired": True,
                }],
            },
        }
        calls = []
        old_jsf = server.call_sire_jsf_form_submitter
        try:
            server.call_sire_jsf_form_submitter = lambda config, prepared_arg, submission_type: calls.append(submission_type) or {
                "ok": True,
                "status": "submitted",
                "receiptReference": "SIRE-OK",
                "payloadSummary": {"recordCount": 1},
                "responseSummary": {"status": "submitted_to_sire_form"},
            }
            config = {"mcp": {"servers": {"hotel_pms": {"env": {
                "SIRE_SUBMITTER_MODE": "jsf_form",
                "SIRE_DOCUMENT_TYPE_VALUE": "1",
                "SIRE_DOCUMENT_NUMBER": "12345678",
                "SIRE_PASSWORD": "secret",
                "SIRE_COMPANY_VALUE": "COMPANY1",
            }}}}}
            result = server.call_sire_submitter(config, prepared, "sire_entrada")
        finally:
            server.call_sire_jsf_form_submitter = old_jsf
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["receiptReference"], "SIRE-OK")
        self.assertEqual(calls, ["sire_entrada"])

    def test_call_sire_submitter_requires_receipt_before_success(self):
        calls = []

        def fake_http_json(url, payload, headers=None, timeout=30):
            calls.append((url, payload, headers))
            return {"success": True, "radicado": "SIRE-123"}

        prepared = {
            "registrationId": "reg-1",
            "reservationId": "res-1",
            "payload": {
                "reservation": {"arrivalDate": "2026-06-26", "departureDate": "2026-06-27"},
                "guests": [{
                    "documentType": "passport",
                    "documentNumber": "A12345678",
                    "birthDate": "1975-09-07",
                    "firstName": "Sensitive",
                    "lastName": "Guest",
                    "nationalityCountry": "United States",
                    "originCountry": "United States",
                    "destinationCountry": "Colombia",
                    "sireRequired": True,
                }],
            },
        }
        old_http = server.http_json
        try:
            server.http_json = fake_http_json
            config = {"mcp": {"servers": {"hotel_pms": {"env": {"SIRE_SUBMISSION_URL": "https://sire-adapter.example/submit", "SIRE_API_TOKEN": "secret"}}}}}
            result = server.call_sire_submitter(config, prepared, "sire_entrada")
        finally:
            server.http_json = old_http
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["receiptReference"], "SIRE-123")
        self.assertEqual(calls[0][0], "https://sire-adapter.example/submit")
        self.assertEqual(calls[0][2]["Authorization"], "Bearer secret")
        self.assertEqual(calls[0][1]["records"][0]["numeroDocumento"], "A12345678")

    def test_registro_daily_pickup_uses_pending_window_and_notifies(self):
        calls: list[tuple[str, dict, str]] = []
        recorded: list[dict] = []
        telegram: list[str] = []

        def fake_local_date(config, offset=0):
            base = dt.date(2026, 6, 26)
            return (base + dt.timedelta(days=offset)).isoformat()

        def fake_pms_tool(config, tool_name, input_payload=None, profile="read"):
            calls.append((tool_name, input_payload or {}, profile))
            if tool_name == "registro_list_pending":
                return [
                    {
                        "registrationId": "reg-1",
                        "reservationId": "res-1",
                        "guestName": "Rishab Whatsapp",
                        "status": "data_submitted",
                        "documentCount": 2,
                        "arrivalDate": "2026-06-26",
                        "departureDate": "2026-07-01",
                        "dueSubmissionTypes": [],
                    },
                    {
                        "registrationId": "old-reg",
                        "reservationId": "old-res",
                        "guestName": "Old Guest",
                        "status": "validated",
                        "documentCount": 1,
                        "arrivalDate": "2026-01-01",
                        "departureDate": "2026-01-02",
                        "dueSubmissionTypes": ["tra"],
                    },
                ]
            if tool_name == "get_reservation":
                return {"reservationId": "res-1", "adultsCount": 2, "childrenCount": 0, "infantsCount": 0}
            if tool_name == "registro_get_by_reservation":
                return {"registrationId": "reg-1", "status": "validated", "dueSubmissionTypes": ["tra"]}
            if tool_name == "registro_list_guests":
                return {"guests": [{"registrationGuestId": "guest-1", "submissionStatus": "ready", "extractionStatus": "extracted", "missingFields": []}]}
            if tool_name == "registro_prepare_government_submission":
                return {
                    "registrationId": "reg-1",
                    "reservationId": "res-1",
                    "submissionType": "tra",
                    "status": "ready",
                    "idempotencyKey": "registro:reg-1:tra",
                    "payload": {"guests": [{"documentNumber": "A12345678"}]},
                }
            if tool_name == "registro_record_submission":
                recorded.append(input_payload or {})
                return {"state": input_payload["state"], "receiptReference": input_payload["receiptReference"]}
            raise AssertionError(f"unexpected PMS tool {tool_name}")

        old_load = server.load_config
        old_pms = server.pms_tool
        old_local_date = server.local_date
        old_enabled = server.government_submitter_enabled
        old_tra = server.call_tra_submitter
        old_extract = server.tool_hotel_registro_extract_reservation
        old_telegram = server.tool_hotel_telegram_send_message
        try:
            server.load_config = lambda: {}
            server.pms_tool = fake_pms_tool
            server.local_date = fake_local_date
            server.government_submitter_enabled = lambda config: True
            server.call_tra_submitter = lambda config, prepared: {
                "ok": True,
                "status": "submitted",
                "receiptReference": "TRA-RECEIPT-1",
                "responseSummary": {"radicado": "TRA-RECEIPT-1"},
            }
            server.tool_hotel_registro_extract_reservation = lambda args: {
                "ok": True,
                "reservationId": args["reservationId"],
                "registrationId": "reg-1",
                "guestCount": 2,
                "documentCount": 2,
                "results": [{"status": "recorded"}, {"status": "recorded"}],
            }
            server.tool_hotel_telegram_send_message = lambda args: telegram.append(args["text"]) or {"ok": True, "message_id": 1}
            result = server.tool_hotel_registro_daily_pickup({"notify": True, "submitTra": True, "maxRecords": 10})
        finally:
            server.load_config = old_load
            server.pms_tool = old_pms
            server.local_date = old_local_date
            server.government_submitter_enabled = old_enabled
            server.call_tra_submitter = old_tra
            server.tool_hotel_registro_extract_reservation = old_extract
            server.tool_hotel_telegram_send_message = old_telegram

        self.assertEqual(result["pendingCount"], 2)
        self.assertEqual(result["eligibleCount"], 1)
        self.assertEqual(result["processed"][0]["label"], "Rishab Whatsapp")
        self.assertEqual(result["processed"][0]["receiptReference"], "TRA-RECEIPT-1")
        self.assertEqual(recorded[0]["state"], "submitted")
        self.assertFalse(telegram)
        self.assertFalse(result["notificationNeeded"])
        self.assertEqual(result["telegram"], {"ok": True, "sent": False, "reason": "no_action_required"})
        self.assertIn("Rishab Whatsapp: enviado tra", result["message"])
        rendered = str(result)
        self.assertNotIn("Old Guest: enviado tra", rendered)
        self.assertTrue(any(call[0] == "registro_list_pending" for call in calls))

    def test_registro_daily_pickup_reports_missing_participant_document(self):
        def fake_local_date(config, offset=0):
            base = dt.date(2026, 7, 11)
            return (base + dt.timedelta(days=offset)).isoformat()

        def fake_pms_tool(config, tool_name, input_payload=None, profile="read"):
            if tool_name == "registro_list_pending":
                return [{
                    "registrationId": "reg-brad",
                    "reservationId": "res-brad",
                    "guestName": "Brad Boyle",
                    "status": "needs_info",
                    "documentCount": 1,
                    "arrivalDate": "2026-07-06",
                    "departureDate": "2026-07-08",
                    "dueSubmissionTypes": [],
                }]
            if tool_name == "get_reservation":
                return {"reservationId": "res-brad", "adultsCount": 2, "childrenCount": 0, "infantsCount": 0}
            raise AssertionError(f"unexpected PMS tool {tool_name}")

        old_load = server.load_config
        old_pms = server.pms_tool
        old_local_date = server.local_date
        old_extract = server.tool_hotel_registro_extract_reservation
        old_telegram = server.tool_hotel_telegram_send_message
        telegram = []
        try:
            server.load_config = lambda: {}
            server.pms_tool = fake_pms_tool
            server.local_date = fake_local_date
            server.tool_hotel_registro_extract_reservation = lambda args: {
                "ok": True,
                "reservationId": args["reservationId"],
                "registrationId": "reg-brad",
                "guestCount": 1,
                "documentCount": 1,
                "results": [{"status": "extracted_with_review_flags"}],
            }
            server.tool_hotel_telegram_send_message = lambda args: telegram.append(args["text"]) or {"ok": True, "message_id": 1}
            result = server.tool_hotel_registro_daily_pickup({"notify": True, "submitTra": False, "daysBack": 7})
        finally:
            server.load_config = old_load
            server.pms_tool = old_pms
            server.local_date = old_local_date
            server.tool_hotel_registro_extract_reservation = old_extract
            server.tool_hotel_telegram_send_message = old_telegram

        self.assertEqual(result["eligibleCount"], 1)
        self.assertEqual(result["needsReview"][0]["expectedGuestCount"], 2)
        self.assertEqual(result["needsReview"][0]["guestCount"], 1)
        self.assertIn("falta documento/registro de 1 huesped", result["needsReview"][0]["reason"])
        self.assertIn("1 huesped necesita revision de extraccion", result["needsReview"][0]["reason"])
        self.assertIn("Brad Boyle", result["message"])
        self.assertTrue(result["notificationNeeded"])
        self.assertEqual(len(telegram), 1)
        self.assertIn("Brad Boyle", telegram[0])

    def test_build_tra_form_fields_maps_required_form_values(self):
        html = """
        <form id="miFormulario">
          <input name="csrfmiddlewaretoken" value="csrf-123">
          <select id="pais"><option value="34">Estados Unidos</option><option value="114">Colombia</option></select>
          <select id="ciudad"><option id="34" value="Boston">Boston</option><option id="114" value=" Manizales - Caldas ">Manizales - Caldas</option></select>
          <select id="pais2"><option value="34">Estados Unidos</option><option value="114">Colombia</option></select>
          <select id="ciudad2"><option id="34" value="Boston">Boston</option><option id="114" value=" Manizales - Caldas ">Manizales - Caldas</option></select>
        </form>
        """
        prepared = {
            "payload": {
                "reservation": {
                    "arrivalDate": "2026-06-26",
                    "departureDate": "2026-07-01",
                    "unitNumber": "1",
                    "totalCostCop": 1000,
                    "travelReason": "tourism",
                },
                "guests": [
                    {
                        "documentType": "passport",
                        "documentNumber": "A12345678",
                        "givenNames": "Miranda",
                        "surname": "Davies",
                        "residenceCountry": "Estados Unidos",
                        "residenceCity": "Boston",
                        "originCountry": "Colombia",
                        "originCity": "Manizales",
                    },
                    {"documentNumber": "B123"},
                ],
            }
        }
        fields, result = server.build_tra_form_fields({}, prepared, html)
        self.assertTrue(result["ok"])
        pairs = fields
        self.assertIn(("tipo_identificacion", "Pasaporte"), pairs)
        self.assertIn(("numero_identificacion", "A12345678"), pairs)
        self.assertIn(("nombres", "Miranda"), pairs)
        self.assertIn(("apellidos", "Davies"), pairs)
        self.assertIn(("ciudad_residencia", "Boston"), pairs)
        self.assertIn(("ciudad_procedencia", " Manizales - Caldas "), pairs)
        self.assertIn(("numero_acompanantes", "1"), pairs)
        self.assertIn(("tipo_acomodacion", "Doble"), pairs)

    def test_build_tra_form_fields_blocks_missing_cost(self):
        html = """
        <input name="csrfmiddlewaretoken" value="csrf-123">
        <select id="pais"><option value="34">Estados Unidos</option></select>
        <select id="ciudad"><option id="34" value="Boston">Boston</option></select>
        <select id="pais2"><option value="34">Estados Unidos</option></select>
        <select id="ciudad2"><option id="34" value="Boston">Boston</option></select>
        """
        prepared = {
            "payload": {
                "reservation": {"arrivalDate": "2026-06-26", "departureDate": "2026-07-01", "unitNumber": "1"},
                "guests": [
                    {
                        "documentType": "passport",
                        "documentNumber": "A12345678",
                        "givenNames": "Miranda",
                        "surname": "Davies",
                        "residenceCountry": "Estados Unidos",
                        "residenceCity": "Boston",
                        "originCountry": "Estados Unidos",
                        "originCity": "Boston",
                    }
                ],
            }
        }
        fields, result = server.build_tra_form_fields({}, prepared, html)
        self.assertEqual(fields, [])
        self.assertFalse(result["ok"])
        self.assertIn("costo", result["missingFields"])

    def test_tra_form_submitter_blocks_missing_credentials(self):
        old_login = server.tra_login_session
        try:
            server.tra_login_session = lambda config: (None, {"ok": False, "status": "blocked", "reason": "tra_credentials_missing"})
            result = server.call_tra_form_submitter({}, {"payload": {}})
        finally:
            server.tra_login_session = old_login
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "tra_credentials_missing")

    def test_build_tra_api_payloads_maps_primary_and_companions(self):
        prepared = {
            "payload": {
                "reservation": {
                    "arrivalDate": "2026-06-26",
                    "departureDate": "2026-07-01",
                    "unitNumber": "1",
                    "totalCostCop": 2000,
                    "travelReason": "tourism",
                },
                "guests": [
                    {
                        "documentType": "passport",
                        "documentNumber": "A12345678",
                        "givenNames": "Miranda",
                        "surname": "Davies",
                        "residenceCity": "Boston",
                        "originCity": "Manizales",
                    },
                    {
                        "documentType": "passport",
                        "documentNumber": "B12345678",
                        "givenNames": "Alex",
                        "surname": "Davies",
                        "residenceCity": "Boston",
                        "originCity": "Manizales",
                    },
                ],
            }
        }
        config = {"mcp": {"servers": {"hotel_pms": {"env": {"TRA_RNT_ESTABLISHMENT": "104360"}}}}}
        primary, companions, result = server.build_tra_api_payloads(config, prepared)
        self.assertTrue(result["ok"])
        self.assertEqual(primary["tipo_identificacion"], "Pasaporte")
        self.assertEqual(primary["numero_identificacion"], "A12345678")
        self.assertEqual(primary["cuidad_residencia"], "Boston")
        self.assertEqual(primary["cuidad_procedencia"], "Manizales")
        self.assertEqual(primary["numero_acompanantes"], "1")
        self.assertEqual(primary["check_in"], "2026-06-26")
        self.assertEqual(primary["check_out"], "2026-07-01")
        self.assertEqual(primary["costo"], "2000")
        self.assertEqual(primary["nombre_establecimiento"], "Owl's Watch")
        self.assertEqual(primary["rnt_establecimiento"], "104360")
        self.assertEqual(len(companions), 1)
        self.assertEqual(companions[0]["padre"], "__PARENT_CODE__")
        self.assertEqual(companions[0]["numero_identificacion"], "B12345678")
        self.assertEqual(companions[0]["cuidad_residencia"], "Boston")
        self.assertEqual(companions[0]["cuidad_procedencia"], "Manizales")
        self.assertEqual(companions[0]["numero_habitacion"], "1")
        self.assertEqual(companions[0]["check_in"], "2026-06-26")
        self.assertEqual(companions[0]["check_out"], "2026-07-01")

    def test_call_tra_api_submitter_posts_one_then_two(self):
        calls = []

        def fake_http_json(url, payload, headers=None, timeout=30):
            calls.append((url, payload, headers))
            if url.endswith("/one/"):
                return {"code": "TRA-PRIMARY-1", "status": "ok"}
            if url.endswith("/two/"):
                return {"status": "ok"}
            raise AssertionError(f"unexpected url {url}")

        prepared = {
            "payload": {
                "reservation": {
                    "arrivalDate": "2026-06-26",
                    "departureDate": "2026-07-01",
                    "unitNumber": "1",
                    "totalCostCop": 2000,
                },
                "guests": [
                    {
                        "documentType": "passport",
                        "documentNumber": "A12345678",
                        "givenNames": "Miranda",
                        "surname": "Davies",
                        "residenceCity": "Boston",
                        "originCity": "Manizales",
                    },
                    {
                        "documentType": "passport",
                        "documentNumber": "B12345678",
                        "givenNames": "Alex",
                        "surname": "Davies",
                        "residenceCity": "Boston",
                        "originCity": "Manizales",
                    },
                ],
            }
        }
        old_http = server.http_json
        try:
            server.http_json = fake_http_json
            config = {"mcp": {"servers": {"hotel_pms": {"env": {"TRA_RNT_ESTABLISHMENT": "104360"}}}}}
            result = server.call_tra_api_submitter(config, prepared, "secret-token")
        finally:
            server.http_json = old_http
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["receiptReference"], "TRA-PRIMARY-1")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "https://pms.mincit.gov.co/one/")
        self.assertEqual(calls[1][0], "https://pms.mincit.gov.co/two/")
        self.assertEqual(calls[0][2]["Authorization"], "token secret-token")
        self.assertEqual(calls[1][1]["padre"], "TRA-PRIMARY-1")


if __name__ == "__main__":
    unittest.main()
