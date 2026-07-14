# PMS Reservation Tool Contract

Hotel expects PMS to expose two narrow tool-runtime tools before production
reservation creation can work.

## `agent_prepare_reservation`

Classification: `draft`

Permissions: `pms.read`

Behavior:

- No reservation database write in v1.
- Normalize and validate the request against PMS rules.
- Re-check unit availability, date logic, duplicate risk, allocation quantities,
  and linked activity dates.
- Accept unit codes `cabin` and `guide-cabin`.
- Reject OTA/channel sources such as Booking.com, Expedia, Airbnb, Beds24, or
  channel-manager-originated bookings.
- Return no finance fields.

Ready response:

```json
{
  "status": "ready",
  "summary": {
    "guestName": "Camilo Martinez",
    "dates": "2026-06-21 to 2026-06-22",
    "bookingType": "overnight_stay",
    "guestCount": 2,
    "units": [{ "unitCode": "cabin", "quantity": 1 }]
  },
  "confirmationCode": "A7K2",
  "preparedToken": "<pms-signed-short-lived-token>",
  "expiresAt": "2026-06-24T21:05:00.000Z",
  "idempotencyKey": "optional-stable-key"
}
```

The `preparedToken` should be signed server-side by PMS and include:

- `typ: "agent_reservation_prepare"`
- `v: 1`
- normalized payload
- confirmation code
- expiry
- minimal source metadata
- canonical JSON payload hash

## `agent_create_reservation`

Classification: `guarded_write`

Permissions: `pms.write`

Behavior:

- Require `preparedToken` and matching `confirmationCode`.
- Reject expired or invalid tokens.
- Revalidate the canonical payload hash.
- Re-check availability at create time.
- Create through the same PMS reservation service used by the UI, not direct
  Prisma writes.
- Use idempotency so repeated confirmation returns the same reservation.
- Tag the reservation internally as agent-created.
- Return no finance fields.

Safe response:

```json
{
  "reservationId": "pms_reservation_id",
  "guestName": "Camilo Martinez",
  "arrivalDate": "2026-06-21",
  "departureDate": "2026-06-22",
  "bookingType": "overnight_stay",
  "units": [{ "unitCode": "cabin", "quantity": 1 }],
  "status": "confirmed",
  "pmsUrl": "https://pms.owlswatch.com/reservations/pms_reservation_id"
}
```

## Defaults

- `source: direct`
- `source: other` only when an explicit operator or non-OTA external reference
  is present
- `commercialTrack: operator` only when operator is explicit
- `payerResponsibility: operator` only when operator is explicit
- `paymentStatus: unpaid`

## Audit

Create audit should record:

- agent name and credential ID
- Telegram chat ID, user ID, and message ID
- confirmation text/code
- normalized payload
- idempotency key
- PMS reservation ID
- tool correlation ID
