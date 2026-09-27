# Finca schedule delivery recovery

The morning report remains scheduled for 07:00 and the check-in for 16:00,
America/Bogota, with a 900-second retry check. The installer refuses a second
system/user owner or an active GUI job. It verifies stopped jobs and explicitly
enables the GUI labels before bootstrap. Install only when no schedule is active;
a job can become due between launchd inspection and replacement.

Both shell runners use `run-finca-tool-job.py`. It holds a kernel lock per job,
freezes the day's report messages, and writes each attempted send before calling
Telegram. Runtime journals live in
`~/.openclaw-finca/schedule-state/finca-daily-{report,checkin}/YYYY-MM-DD.json`
with private permissions. They contain report text and delivery receipts; never
commit them or copy them into logs or issue reports. Preserve journals and the
existing `schedule-stamps` directory during deployment.

A confirmed Telegram message ID marks that message sent. A retry skips those
messages and uses the original frozen content for the remainder. Only an explicit
Telegram rejection with code 400, 401, 403, 404 or 429 proves non-delivery; only
retryable rejections are automatically retried, with at most three attempts per
message. A timeout, 5xx, malformed receipt, crash during sending, or failure to
save a receipt leaves an unknown outcome and blocks automatic resending.

The daily stamp is written only after every message has a durable receipt.
Existing stamps remain authoritative. `--force` bypasses the enable/time gates
but does not bypass stamps, delivery journals or locks. Reports over 20 chunks,
invalid journals and destination changes stop for inspection rather than
truncating or redirecting notifications. Logs contain only fixed codes/counts.

When a job reports `delivery_outcome_unknown`, inspect the staff conversation
and the private journal before taking action. Do not delete the journal or use
`--force` to replay it. After establishing the outcome, an operator may correct
the affected row under the same job lock: `sent` requires the actual positive
Telegram `messageId`; `not_sent` with `retryable: true` requires positive evidence
of non-delivery. Preserve the frozen text/digest and attempts. If evidence cannot
resolve delivery, leave the job blocked. Permanent rejections and exhausted
attempts likewise require investigation before changing retry state.

Tests use fake server/launchctl implementations, temporary state, and no external
calls: `python3 -m unittest discover -s tests -p 'test_finca_schedule*.py'`.
