import importlib.util
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

    def test_normalize_reservation_uses_booking_type_from_list_row(self):
        normalized = server.normalize_reservation(
            {
                "reservationId": "res-123",
                "guestName": "Sergio Henao",
                "bookingType": "day_pass",
                "nights": 1,
            },
            {"reservation": {"reservationId": "res-123", "guestName": "Sergio Henao"}},
            {},
            "arrival",
        )
        self.assertEqual(normalized["bookingCategory"], "day_pass")
        self.assertEqual(normalized["visitPhrase"], "pasadía")

    def test_same_day_activities_are_not_lodging_movements(self):
        rows = [
            {"reservationId": "day-pass", "bookingType": "day_pass", "guestName": "Sergio"},
            {"reservationId": "bird-tour", "bookingType": "bird_tour", "guestName": "Poorvi"},
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

    def test_registro_submission_plan_no_due_is_no_due_when_complete(self):
        plan = server.build_registro_submission_plan(
            {"registrationId": "reg-1", "status": "complete", "dueSubmissionTypes": []},
            [{"role": "primary", "submissionStatus": "ready", "missingFields": []}],
        )
        self.assertEqual(plan["status"], "no_due")

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


if __name__ == "__main__":
    unittest.main()
