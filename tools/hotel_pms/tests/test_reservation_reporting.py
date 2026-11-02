import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reservation_reporting import booking_groups

spec = importlib.util.spec_from_file_location("hotel_reporting_server", ROOT / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def reservation(rid, **changes):
    return dict({
        "reservationId": rid, "guestName": "Example Guest",
        "bookingType": "overnight_stay", "status": "confirmed",
        "arrivalDate": "2027-02-01", "departureDate": "2027-02-02",
        "adultsCount": 2, "childrenCount": 0,
        "unitAllocations": [{"unitCode": "cabin", "quantity": 1}],
    }, **changes)


class ReportingTests(unittest.TestCase):
    def test_same_name_and_dates_preserve_two_cabins(self):
        group, = booking_groups([reservation("one"), reservation("two")])
        self.assertEqual(group["reservationIds"], ["one", "two"])
        self.assertEqual(group["reservationCount"], 2)
        self.assertEqual(group["unitSummaryEs"], "2 cabañas")
        self.assertEqual(group["unitCounts"], {"cabin": 2})
        self.assertNotIn("guestCount", group)

    def test_repeat_id_counts_once_but_conflicting_rows_have_no_unit_total(self):
        row = reservation("one")
        group, = booking_groups([row, dict(row)])
        self.assertEqual(group["reservationCount"], 1)
        self.assertEqual(group["unitSummaryEs"], "1 cabaña")
        group, = booking_groups([row, reservation("one", unitAllocations=[])])
        self.assertIsNone(group["unitCounts"])

    def test_unknown_allocations_do_not_create_partial_or_invented_total(self):
        for allocations in (None, [], [{}], [{"unitCode": "cabin"}],
                            [{"unitCode": "cabin", "quantity": True}],
                            [{"unitCode": "cabin", "quantity": 0}],
                            [{"unitCode": "cabin", "quantity": "1"}],
                            [{"unitCode": "unknown", "quantity": 1}]):
            with self.subTest(allocations=allocations):
                group, = booking_groups([reservation("one"), reservation("two", unitAllocations=allocations)])
                self.assertIsNone(group["unitCounts"])
                self.assertIsNone(group["unitSummaryEs"])

    def test_mixed_units_and_multiple_units_in_one_booking(self):
        group, = booking_groups([reservation("one", unitAllocations=[
            {"unitCode": "cabin", "quantity": 2},
            {"unitCode": "guide-cabin", "quantity": 1},
        ])])
        self.assertEqual(group["unitCounts"], {"cabin": 2, "guide-cabin": 1})
        self.assertEqual(group["unitSummaryEs"], "2 cabañas, 1 habitación de guía")

    def test_different_dates_status_type_and_missing_identity_stay_separate(self):
        for changes in ({"arrivalDate": "2027-02-03"}, {"status": "cancelled"},
                        {"bookingType": "day_pass"}, {"isOvernight": False},
                        {"guestName": None}, {"arrivalDate": None}, {"reservationId": None}):
            with self.subTest(changes=changes):
                rows = [reservation("one"), dict(reservation("two"), **changes)]
                self.assertEqual(len(booking_groups(rows)), 2)

    def test_all_list_tools_supply_identity_based_counts(self):
        rows = [reservation("one"), reservation("two")]
        for function, key in ((server.tool_hotel_pms_list_arrivals, "arrivals"),
                              (server.tool_hotel_pms_list_departures, "departures"),
                              (server.tool_hotel_pms_list_in_house, "inHouse"),
                              (server.tool_hotel_pms_list_reservations, "reservations"),
                              (server.tool_hotel_pms_find_reservation, "matches")):
            with self.subTest(tool=function.__name__), patch.object(server, "load_config", return_value={}), patch.object(server, "pms_tool", return_value=rows):
                result = function({})
                self.assertEqual(result["bookingGroups"][key][0]["unitSummaryEs"], "2 cabañas")
                self.assertEqual(len(result[key]), 2)

    def test_summary_counts_movements_separately(self):
        rows = [reservation("one"), reservation("two")]
        def pms(config, name, args):
            if name == "list_arrivals": return rows
            if name == "list_departures": return [reservation("departure")]
            if name == "list_in_house_guests": return []
            return {"reservation": reservation(args["reservationId"])}
        with patch.object(server, "load_config", return_value={}), patch.object(server, "pms_tool", side_effect=pms):
            result = server.tool_hotel_pms_get_tomorrow_summary({"date": "2027-02-01"})
            arrivals = server.tool_hotel_pms_get_tomorrow_arrivals({"date": "2027-02-01"})
        self.assertEqual(result["bookingGroups"]["arrivals"][0]["unitSummaryEs"], "2 cabañas")
        self.assertEqual(result["bookingGroups"]["departures"][0]["unitSummaryEs"], "1 cabaña")
        self.assertEqual(result["bookingGroups"]["stayovers"], [])
        self.assertEqual(arrivals["bookingGroups"]["arrivals"][0]["reservationCount"], 2)

    def test_safe_context_preserves_legacy_id(self):
        self.assertEqual(server.safe_reservation_summary({"id": "legacy"})["reservationId"], "legacy")


if __name__ == "__main__":
    unittest.main()
