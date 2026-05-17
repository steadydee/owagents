# Security Boundaries

Owl's Watch agents are clerks. Operations, Luna, Gmail, Google Drive, and Telegram remain the systems of record or transport.

## Agent Authority

- `main`: conductor only. No business side-effect tools.
- `cuenta`: creates expense drafts only.
- `cotiza`: creates quote drafts and Drive quote sheets only.
- `correo`: creates Email Desk tasks and Gmail drafts only. It does not send final emails.

## Main

Allowed:

- Explain the Owl's Watch agent setup.
- Route people to the right Telegram topic or specialist.
- Answer lightweight operational questions about which agent handles what.

Forbidden:

- Create, approve, modify, reject, or delete expenses.
- Create quote drafts, revise quote sheets, or change pricing.
- Create, update, or send email drafts.
- Access Operations, Luna, Gmail, Drive, or Telegram side-effect tools.
- Use shell, browser, filesystem, gateway, cron, node, or web tools.
- Claim to have changed agent code/config/routing. System changes belong in this repo and are deployed by Codex.

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

## Tool Policy

Each agent uses `tools.profile: "minimal"` plus explicit `alsoAllow` entries for narrow `owlswatch_*` tools.

Broad tools stay denied by default:

- `exec`
- `browser`
- `gateway`
- `cron`
- `nodes`
- `canvas`
- `group:fs`
- `group:web`
- `bundle-mcp`

Any change that broadens tools must include:

- the reason
- the exact agent
- the exact tool names
- a smoke test
- a review of whether a narrower `owlswatch_*` tool would be safer

## Secrets

Never commit tokens, service-account JSON, auth profiles, runtime sessions, memory logs, receipt spools, raw Gmail content, or generated quote sheets.
