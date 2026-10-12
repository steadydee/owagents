# Users

Dennis is the primary receipt submitter.

Adriana is the secondary receipt submitter.

Receipt submission authorization comes from configured numeric Telegram sender IDs, not these names or a Telegram display name. The trusted intake plugin verifies the current sender against the configured `channels.telegram.allowFrom` and `channels.telegram.groupAllowFrom` entries. Use its current-turn result; never infer authorization from this document or carry another sender's acceptance or refusal forward.
