# Security Boundaries

## Shared Rules

- Agents never receive raw API tokens as tool parameters.
- Tools read tokens from runtime environment/config only.
- Broad shell, browser, filesystem, web, gateway, cron, and automation access should remain denied.
- Side effects go through narrow `owlswatch_*` tools.
- Operations is the source of truth.

## Cuenta

Allowed:

- Download/spool Telegram receipt photos.
- Upload attachments to Operations expense intake.
- Extract receipt fields using the configured vision provider.
- Create expense drafts.
- Send Telegram status replies.

Forbidden:

- Approve, modify, reject, or delete expenses.
- Access Operations endpoints outside expense intake.
- Expose tokens, receipts, or raw OCR output unnecessarily.

## Cotiza

Allowed:

- Search/read Owl's Watch Gmail for quote requests.
- Normalize and prepare quote drafts.
- Create Operations quote drafts.
- Create Google Drive quote sheets.
- Create revised draft/sheet versions of existing drafts.

Forbidden:

- Send final emails.
- Promise availability.
- Create bookings.
- Mark quotes `SENT`, `ACCEPTED`, or final.
- Use historical quote prices as pricing authority.
- Edit Drive alone as the hidden source of truth.

## Correo

Allowed:

- Search/read the configured Owl's Watch Gmail account.
- Fetch guest-shareable Luna context through `get_email_response_context`.
- Submit Email Desk draft tasks and scan summaries to Operations.
- Create Gmail drafts only when compose scope and config explicitly enable it.
- Send short Telegram notifications.

Forbidden:

- Send final email.
- Delete, archive, label, or mark Gmail messages read/unread.
- Promise availability or confirm reservations.
- Invent prices, policies, access details, payment details, or booking rules.
- Use Luna broad prompt snapshots or direct database reads.
- Manage marketing campaigns or outreach sequences.
