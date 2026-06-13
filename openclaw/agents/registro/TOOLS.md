# Registro Tools

Allowed tools:

- `registro_list_pending`: current PMS rows needing extraction or due SIRE work.
- `registro_get`: one PMS registration with reservation/submission context.
- `registro_fetch_media`: reads the PMS media reference without exposing image bytes.
- `registro_parse_mrz`: deterministic TD3 MRZ parser and checksum validator.
- `registro_extract_document_vision`: local or tailnet-only vision extractor.
- `registro_delete_media`: deletes a contained transient local media file only
  after any required durable evidence copy is confirmed.
- `registro_record_extraction`: writes extracted/validated fields to PMS.
- `registro_set_status`: moves PMS registration status through guarded transitions.
- `registro_flag_exception`: records PMS exception state.
- `registro_record_submission`: records SIRE/TRA attempt state or receipt references.
- `registro_request_guest_fix`: asks Luna to request a guest correction inside the WhatsApp window.
- `registro_telegram_notify`: staff-only Telegram notification.
- `browser`: approved exception for SIRE only, restricted by policy to `apps.migracioncolombia.gov.co`; use only after the recon-gated SIRE routine is implemented.

Forbidden:

- Direct PMS or Luna database access.
- Browser navigation outside `apps.migracioncolombia.gov.co`.
- SIRE browser automation before the recon-gated routine is implemented.
- Guest messaging outside Luna.
- Telegram messages containing full document images, raw IDs, tokens, or full government payloads.
- PMS tools outside `registro` classification.
- Shell, generic web, filesystem, gateway, cron, node, canvas, or broad MCP tools.
