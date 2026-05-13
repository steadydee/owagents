# Smoke Test Correo

Run local source checks:

```sh
./scripts/smoke-correo.sh
```

Manual production test:

1. Send a test email from an external address to the configured Owl's Watch Gmail account.
2. In the Email Telegram topic, ask Correo to process the latest test email.
3. Confirm the Telegram reply starts with `New email draft`.
4. Confirm an Email Desk task appears in Operations `/emails`.
5. Confirm the task includes contact, summary, last client message time, and a message snapshot.
6. Confirm the draft is not sent automatically.

Keep scheduled polling disabled while testing one email at a time.
