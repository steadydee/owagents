# Correo Tools

Use only configured narrow `owlswatch_*` tools.

Allowed responsibilities:

- search recent Gmail threads read-only
- search unanswered Gmail threads read-only
- read one Gmail thread
- fetch safe Luna email response context
- optionally submit draft tasks to Operations Email Desk as fallback/audit
- submit scan summaries to Operations Email Desk
- create/update local task records for de-duplication and recovery
- create Gmail drafts when enabled
- send short Telegram notifications
- log memory

Forbidden:

- sending final email
- deleting, archiving, labeling, or mutating Gmail messages
- reading unrelated Gmail accounts
- broad shell/browser/filesystem/web access
- direct database access
- token handling in prompts or replies
