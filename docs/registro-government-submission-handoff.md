# Registro Government Submission Handoff

This note describes the current OpenClaw Hotel agent boundary after PMS added
government-submission payload preparation. The agent now includes a guarded
submitter wrapper, but live submission remains disabled until the government
adapter credentials/endpoints/selectors are configured and verified.

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

- `hotel_registro_prepare_government_submission`
  - Calls PMS `registro_prepare_government_submission`.
  - PMS prepares the official TRA/SIRE payload.
  - The Hotel wrapper returns only staff-safe metadata.
  - It deliberately omits guest identity payloads, document numbers, file
    bytes, fetch tokens, raw OCR, and government form data from model context.
  - Does not submit to government systems.

- `hotel_registro_submit_government`
  - Calls PMS `registro_prepare_government_submission` internally.
  - `mode: dry_run` verifies readiness and returns safe metadata only.
  - `mode: submit` is receipt-gated and requires the runtime flag
    `REGISTRO_GOVERNMENT_SUBMITTER_ENABLED=1`.
  - TRA prefers the official MinCIT PMS API when `TRA_API_TOKEN` is configured.
    The official adapter posts the primary guest to `https://pms.mincit.gov.co/one/`
    and accompanying guests to `https://pms.mincit.gov.co/two/`.
  - Until the official API token is available, a conservative TRA manual-form
    adapter can use runtime-only TRA credentials and records submitted only
    after the guest is visible in TRA's registered-guests table.
  - SIRE validates the PMS-prepared `Alojamiento y Hospedaje` payload internally
    and can call a configured adapter endpoint, but live SIRE remains blocked
    until that endpoint or browser routine is verified.
  - PMS is marked submitted only after a real receipt/reference is returned.
  - It never returns guest identity payloads to the model.

- `hotel_registro_record_submission_status`
  - Records `pending`, `failed`, or `needs_info` attempts in PMS.
  - Deliberately rejects `submitted`.
  - `hotel_registro_submit_government` is the only agent-side path that can
    record `submitted`, and only after receiving a real government
    receipt/reference.

## PMS Contract Implemented

PMS now exposes:

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
  "receiptGranularity": "registration",
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

The Hotel wrapper has been tested against production PMS for the Rishab
reservation:

```text
reservationId: 4469d2c7-54b6-43ac-8d0d-c50639a0548f
registrationId: de99c5d8-d26b-414e-9282-7cb7023659ae
status: ready
due: tra, sire_entrada
guests: 2 ready / 2 total
```

## Submission Recording

PMS now requires a real receipt/reference for `submitted` attempts and handles
idempotent retries safely.

The manual status tool records only:

- `pending`
- `failed`
- `needs_info`

It rejects `submitted` because submitted status must come from the receipt-gated
submitter.

## Government Submitter Boundary

The live submitter remains agent-side in the narrow Hotel PMS tool runtime. PMS
does not store government portal credentials.

Flow:

```text
Hotel agent
  -> PMS registro_prepare_government_submission
  -> hotel_registro_submit_government
  -> SIRE/TRA adapter with credentials/selectors/API
  -> PMS registro_record_submission(state=submitted, receiptReference=...)
```

The Hotel agent must not claim completion until the final PMS recording step
contains a real official receipt/reference.

## Runtime Configuration

Live submission is off by default.

```text
REGISTRO_GOVERNMENT_SUBMITTER_ENABLED=0
TRA_API_BASE_URL=https://pms.mincit.gov.co
TRA_API_TOKEN_FILE=~/.openclaw-hotel/secrets/tra-api-token
TRA_ESTABLISHMENT_NAME=Owl's Watch
TRA_RNT_ESTABLISHMENT=<rnt_number>
TRA_SUBMISSION_URL=<optional custom compatibility endpoint>
TRA_USERNAME_FILE=~/.openclaw-hotel/secrets/tra-username
TRA_PASSWORD_FILE=~/.openclaw-hotel/secrets/tra-password
TRA_LOGIN_URL=https://tra.mincit.gov.co/login/
TRA_NEW_GUEST_URL=https://tra.mincit.gov.co/padd/
TRA_REGISTERED_GUESTS_URL=https://tra.mincit.gov.co/blo
SIRE_LOGIN_URL=https://apps.migracioncolombia.gov.co/sire/public/login.jsf
```

Do not enable `REGISTRO_GOVERNMENT_SUBMITTER_ENABLED` until at least one adapter
has been verified in a non-destructive run.

TRA's token self-service page is available at:

```text
https://pms.mincit.gov.co/token/
```

It uses the RNT and reCAPTCHA, then sends the token to the registered RNT email.
Store the resulting API token in a local secret file only.

The PMS payload must include the internal TRA `costo`/total value required by
TRA. This value is for government submission only and must not be exposed in
Telegram, agent memory, or staff hotel summaries.

## Remaining Non-PMS Work

Finish the government adapters after we have verified credentials and behavior:

- TRA API token from MinCIT's PMS token manager
- SIRE credentials plus browser/API selectors and receipt shape
- entrada versus salida flow
- duplicate behavior
- failure messages and retry safety

Until then, the production-safe flow is:

```text
Hotel agent -> extract/validate Registro -> dry-run government submitter -> staff sees ready/not ready
```
