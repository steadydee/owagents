# Registro Compliance Tools

Narrow OpenClaw tools for the Registro compliance agent.

The tools call PMS and Luna through their app tool runtimes using short-lived
HMAC machine tokens. They do not connect to either database directly.

## Tools

- `registro_list_pending`
- `registro_get`
- `registro_fetch_media`
- `registro_parse_mrz` (TD3 passports and TD1 cards)
- `registro_extract_document_vision`
- `registro_delete_media`
- `registro_record_extraction`
- `registro_set_status`
- `registro_flag_exception`
- `registro_record_submission`
- `registro_request_guest_fix`
- `registro_telegram_notify`

## Runtime Env

- `PMS_BASE_URL`
- `LUNA_BASE_URL`
- `PMS_PROPERTY_ID`
- `OW_AGENT_TOKEN_SECRET` or `OW_AGENT_TOKEN_SECRET_FILE`
- `REGISTRO_VISION_ENDPOINT` optional local/tailnet extractor
- `REGISTRO_VISION_TOKEN` optional
- `CHAKRA_ACCESS_TOKEN` optional, enables WhatsApp media download to local spool
- `CHAKRA_PLUGIN_ID` optional, enables WhatsApp media download to local spool
- `REGISTRO_TELEGRAM_BOT_TOKEN`
- `REGISTRO_TELEGRAM_NOTIFY_CHAT_ID`
- `REGISTRO_TELEGRAM_NOTIFY_THREAD_ID` optional

Tokens and media spools are runtime-only. Do not commit them.
