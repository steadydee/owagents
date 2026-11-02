"""Count booked units by reservation identity, never by guest-name uniqueness."""
from __future__ import annotations

from typing import Any


UNIT_LABELS = {"cabin": ("cabaña", "cabañas"), "guide-cabin": ("habitación de guía", "habitaciones de guía")}


def booking_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Display groups only: matching names/dates do not establish guest identity.

    Repeated IDs count once. Missing IDs, dates, or names stay separate. Unknown
    allocations never become an invented one-unit booking or a partial total.
    """
    unique: dict[Any, dict[str, Any]] = {}
    conflicts = set()
    for index, row in enumerate(rows):
        rid = row.get("reservationId") or row.get("id")
        key = ("id", rid) if rid else ("row", index)
        if key in unique:
            if row != unique[key]:
                conflicts.add(key)
        else:
            unique[key] = row

    grouped: dict[Any, list[Any]] = {}
    for identity, row in unique.items():
        start = row.get("visitDate") or row.get("arrivalDate")
        end = row.get("departureDate")
        kind = row.get("bookingType")
        complete = row.get("guestName") and start and (
            (kind == "overnight_stay" and end) or kind in ("day_pass", "bird_tour")
        )
        key = (row.get("guestName"), start, end, kind, row.get("isOvernight"), row.get("status"))
        if not complete or identity[0] != "id" or identity in conflicts:
            key = ("ungrouped", identity)
        grouped.setdefault(key, []).append(identity)

    result = []
    for identities in grouped.values():
        first = unique[identities[0]]
        counts: dict[str, int] = {}
        complete = True
        for identity in identities:
            row = unique[identity]
            allocations = row.get("unitAllocations")
            if identity in conflicts or not isinstance(allocations, list) or not allocations:
                complete = False
                continue
            for allocation in allocations:
                if not isinstance(allocation, dict):
                    complete = False
                    continue
                code, quantity = allocation.get("unitCode"), allocation.get("quantity")
                if code not in UNIT_LABELS or type(quantity) is not int or quantity <= 0:
                    complete = False
                    continue
                counts[code] = counts.get(code, 0) + quantity
        unit_summary = ", ".join(
            f"{quantity} {UNIT_LABELS[code][0 if quantity == 1 else 1]}"
            for code, quantity in sorted(counts.items())
        ) if complete else None
        result.append({
            "guestName": first.get("guestName"),
            "arrivalDate": first.get("arrivalDate"),
            "departureDate": first.get("departureDate"),
            "visitDate": first.get("visitDate"),
            "bookingType": first.get("bookingType"),
            "reservationIds": [key[1] for key in identities if key[0] == "id"],
            "reservationCount": len(identities),
            "unitCounts": counts if complete else None,
            "unitSummaryEs": unit_summary,
        })
    return result


def with_booking_groups(result: dict[str, Any], *collections: str) -> dict[str, Any]:
    result["bookingGroups"] = {key: booking_groups(result[key]) for key in collections}
    return result
