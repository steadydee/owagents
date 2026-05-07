# Owl's Watch Cuenta Profile

Cuenta is the Telegram receipt intake clerk for Owl's Watch Operations. Cuenta creates expense drafts only. Operations remains the source of truth, and humans review drafts at `https://operations.owlswatch.com/expenses`.

## How to Send Receipts

Spanish:

`Taxi aeropuerto 42.000 COP`

English:

`Client lunch, paid with card`

Send one receipt photo, or a Telegram photo album, with a short note when useful. A caption is helpful but not required.

## What Happens

On extraction success, Cuenta uploads the preserved receipt photo, extracts vendor/date/total/currency when clear, creates a draft, and replies with the review URL.

On extraction failure, Cuenta still uploads the preserved photo and creates a draft with null or flagged fields. Receipts are not discarded just because OCR is uncertain.

## Boundaries

Cuenta never approves, modifies, or deletes expenses. Cuenta does not access Operations endpoints other than:

- `POST /api/expenses/attachments/upload`
- `POST /api/expenses/intake`

Cuenta does not provide financial advice and does not discuss approvals.

## Configuration

Profile config:

`~/.openclaw-owlswatch/openclaw.json`

Workspace:

`~/.openclaw/workspace-owlswatch`

Telegram placeholders are configured under `channels.telegram`:

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "botToken": "<TELEGRAM_BOT_TOKEN_FILLED_MANUALLY>",
      "dmPolicy": "allowlist",
      "allowFrom": ["<dennis_chat_id>", "<adriana_chat_id>"]
    }
  }
}
```

The OpenClaw schema in this install does not permit arbitrary top-level custom blocks, so Operations settings are stored in the MCP server environment block:

```json
{
  "mcp": {
    "servers": {
      "owlswatch_intake": {
        "env": {
          "OPERATIONS_API_BASE_URL": "https://operations.owlswatch.com",
          "EXPENSE_INTAKE_API_TOKEN": "<EXPENSE_INTAKE_API_TOKEN_FILLED_MANUALLY>",
          "OWLSWATCH_VISION_ENDPOINT": "<VISION_ENDPOINT_FILLED_MANUALLY>",
          "OWLSWATCH_VISION_API_KEY": "<VISION_API_KEY_FILLED_MANUALLY>",
          "OWLSWATCH_VISION_MODEL": "<VISION_MODEL_FILLED_MANUALLY>"
        }
      }
    }
  }
}
```

To rotate the Telegram bot token, allowlist, Operations token, or vision model, edit the profile config and reload/restart the OpenClaw gateway or MCP runtime as needed. The tool server reads config on each tool call; environment variables supplied only at process startup require a process restart.

## Narrow-Tool Architecture

Cuenta can call only `session_status` and the `owlswatch_*` tools. Tokens never appear in the agent context because tool calls do not accept token parameters and broad filesystem/shell/web tools are denied.

In this OpenClaw build, configured MCP servers are exposed through the `bundle-mcp` runtime with provider-safe names like `owlswatch_intake__owlswatch_telegram_get_file`. Because of that, `bundle-mcp` is not globally denied in the live config; the concrete prefixed owlswatch tool names are explicitly allowlisted instead. If these tools are later registered as native plugin tools under their unprefixed names, restore the stricter `bundle-mcp` deny entry.

Receipt photos are saved durably under:

- `spool/intake/{source_message_id}/`
- `spool/media-groups/{chat_id}/{media_group_id}/`

Album handling stores every arriving photo, waits for the quiet period inside the album check tool, and atomically claims the album so only one run creates the draft.

## Manual Test Plan

1. Single photo with caption: send one clear receipt photo and a note. Expect upload, extraction, draft creation, Telegram review reply, and memory log.
2. Multi-photo album: send two or more receipt photos as one Telegram album. Confirm only one draft is created and the album claim state names a single owner.
3. Photo with no caption: send a receipt photo alone. Expect draft creation and a concise reply in the default language.
4. Repeat send: resend the same message/album path or replay the same metadata. Confirm idempotency prevents duplicate expense drafts.
5. Message with no photo: send text only. Expect only the localized instruction to send a receipt photo.
6. Illegible receipt: send a deliberately unclear image. Expect `extraction_status: "failed"` or flags, but still a draft with attachments.
7. Operations API offline: temporarily point `OPERATIONS_API_BASE_URL` to an unavailable endpoint. Confirm photos remain in `spool/intake` and the user receives a preservation/error reply.
8. Token rotation: replace placeholders with new token values, reload/restart as appropriate, and confirm no code changes are needed.

## Manual Setup Still Required

- Create or confirm the Telegram bot token in BotFather.
- Capture Dennis's numeric Telegram chat ID.
- Capture Adriana's numeric Telegram chat ID.
- Fill in `EXPENSE_INTAKE_API_TOKEN`.
- Fill in the configured vision endpoint/API key/model.
- Reload/restart OpenClaw if using environment variables that are only read at process startup.

## Profile Creation Commands

The requested interactive command was run:

```sh
openclaw --profile owlswatch onboard --workspace ~/.openclaw/workspace-owlswatch
```

It reached the interactive risk gate and exited on the default `No`. The profile was then created non-interactively with:

```sh
openclaw --profile owlswatch onboard --workspace ~/.openclaw/workspace-owlswatch --non-interactive --accept-risk --mode local --flow quickstart --auth-choice skip --skip-channels --skip-daemon --skip-health --skip-search --skip-skills --skip-ui --json
```

Validation commands:

```sh
openclaw --profile owlswatch doctor
openclaw --profile owlswatch config validate
openclaw --profile owlswatch skills check
openclaw --profile owlswatch channels list --no-usage
openclaw --profile owlswatch channels status --probe
openclaw --profile owlswatch security audit
```
