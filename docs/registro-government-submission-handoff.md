# Registro Government Submission Handoff

This note describes the current OpenClaw Hotel agent boundary and the PMS
contract still needed before live SIRE/TRA submission automation.

## Agent Side Now Built

The Hotel OpenClaw agent has these Registro tools:

- `hotel_registro_extract_reservation`
  - Fetches Registro guest documents through scoped PMS document tokens.
  - Extracts identity fields.
  - Records guest-level extraction back to PMS.

- `hotel_registro_prepare_submissions`
  - Reads the PMS Registro record and structured guests.
  - Confirms the record is validated.
  - Confirms every guest is `submissionStatus: ready`.
  - Returns due submission types such as `tra` and `sire_entrada`.
  - Returns a staff-safe staged plan only.
  - Does not submit to government systems.

- `hotel_registro_record_submission_status`
  - Records `pending`, `failed`, or `needs_info` attempts in PMS.
  - Deliberately rejects `submitted`.
  - A future live submitter must be the only path that records `submitted`,
    and only after receiving a real government receipt/reference.

## PMS Contract Needed For Live Submission

Please add a PMS tool that prepares official submission payloads without making
the agent infer government field mappings from broad Registro records.

Suggested tool:

```text
registro_prepare_government_submission
```

Input:

```json
{
  "registrationId": "uuid",
  "submissionType": "tra | sire_entrada | sire_salida"
}
```

Output:

```json
{
  "registrationId": "uuid",
  "submissionType": "tra",
  "status": "ready | needs_info | blocked",
  "idempotencyKey": "stable-key",
  "payload": {
    "property": {},
    "reservation": {},
    "guests": []
  },
  "missingFields": [],
  "warnings": []
}
```

Rules:

- Return only fields required by TRA/SIRE.
- Do not include document image bytes, fetch tokens, public URLs, or raw OCR.
- Include one structured guest object per guest.
- Include stable idempotency material for retries.
- If the submission is not due, return `blocked`.
- If a required field is missing, return `needs_info`.
- Preserve current PMS role as source of truth for origin/destination,
  travel reason, occupation, stay dates, and guest identity fields.

## Submission Recording Tightening

Current `registro_record_submission` appends attempts. For live automation,
please add idempotency so retrying the same official submission cannot create
misleading duplicate attempts.

Recommended additions:

- `idempotencyKey` input.
- Unique constraint or service-level dedupe by:
  - `registrationId`
  - `submissionType`
  - `idempotencyKey`
- Clear return shape when an idempotent duplicate is replayed.

Please also clarify granularity:

- If SIRE/TRA receipts are per registration, keep registration-level attempts.
- If receipts are per guest, add `registrationGuestId` support.

## PMS UI Needed

Show submission state in the Registro/Documents area:

- due submission types
- pending attempts
- failed attempts and error messages
- submitted receipt/reference
- attempted/submitted timestamps
- actor/source

## Government Submitter Boundary

The live submitter should remain agent-side or in a narrow worker controlled by
the agent runtime. PMS should not store government portal credentials.

Flow:

```text
Hotel agent
  -> PMS registro_prepare_government_submission
  -> SIRE/TRA adapter with credentials/selectors/API
  -> PMS registro_record_submission(state=submitted, receiptReference=...)
```

The Hotel agent must not claim completion until the final PMS recording step
contains a real official receipt/reference.

