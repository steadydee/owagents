# Cotiza

Cotiza is the Owl's Watch quote-drafting agent. It turns Gmail quote requests, pasted WhatsApp conversations, and Telegram `/cotiza` requests into draft quote records for human review.

Cotiza does not send email, promise availability, create bookings, approve quotes, or override pricebook rules. Operations calculates prices and tracks quote status. Google Drive stores the editable draft sheet.

## How To Use

Telegram examples:

- `/cotiza paste` followed by a WhatsApp conversation.
- `/cotiza linda may 5` to search Owl's Watch Gmail.
- `/cotiza operator` followed by a request that should use operator pricing.
- `/cotiza direct` followed by a request that should use direct-client pricing.

Spanish example:

`/cotiza operador pareja dos noches cabana comida incluida y pajareo todos los dias`

English example:

`/cotiza operator couple two nights cabin meals included and birding every day`

## What Happens

Cotiza extracts the request, calls Operations to calculate pricing, creates an Operations quote row, creates a Google Drive draft sheet, patches the Drive link back into Operations, and replies with the review links.

If extraction is incomplete, Cotiza either asks one short question or creates a draft with visible missing fields. If Drive creation fails after the Operations row is created, the quote row remains for review.

Review quotes at:

https://operations.owlswatch.com/quotes

## Configuration

Configured values live in `~/.openclaw-owlswatch/openclaw.json` or the tool runtime environment.

Required:

- `QUOTE_INTAKE_API_TOKEN`
- `OPERATIONS_BASE_URL=https://operations.owlswatch.com`
- `OWLSWATCH_GMAIL_ACCOUNT=info@owlswatch.com`
- `GOOGLE_DRIVE_QUOTES_FOLDER_ID`
- `GOOGLE_APPLICATION_CREDENTIALS`

Optional:

- `GOOGLE_DRIVE_QUOTE_TEMPLATE_SPREADSHEET_ID`
- `OWLSWATCH_QUOTES_MOCKS=1` for local contract tests only

In this installed OpenClaw version, arbitrary top-level config blocks such as `operations`, `gmail`, and `googleDrive` do not validate. Cotiza's runtime values are therefore stored under `mcp.servers.owlswatch_quotes.env`.

For Gmail, the Google credentials must allow read-only access to `info@owlswatch.com`. For Drive, share the configured quote folder with the service account if a service account owns the API calls.

Tokens stay inside the tool process. The model never receives token values as tool parameters, prompt text, memory, or Telegram replies.

## Tool Architecture

Cotiza only uses narrow tools:

- Gmail search/read
- quote preparation, validation, and pricing preview
- quote draft creation, Drive sheet creation, and Operations Drive-link patching
- Telegram reply
- Cotiza memory log

Broad shell, browser, database, web, and automation tools are denied by the OpenClaw profile policy.

## Routing

Preferred production routing is a private Telegram group with forum topics:

- Receipts topic -> Cuenta
- Quotes topic -> Cotiza

Until group and topic IDs are configured, Cotiza is available as a separate OpenClaw agent and its `/cotiza` skill/tooling is installed.

## Manual Test Plan

- `/cotiza paste` with a simple couple/two-night/cabin/meals/birding request.
- `/cotiza linda may 5` with Gmail search enabled.
- Unknown audience should trigger one question.
- Missing dates should create a needs-info draft or ask one question.
- Bilingual guide should be flagged as approximate and subject to availability.
- Transport requested without a price should be marked missing, never invented.
- Drive failure should keep the Operations quote row.
- Operations API failure should not report success.
- Duplicate Gmail thread should return or update the existing idempotent quote.
- Receipt photos should still route to Cuenta, not Cotiza.
