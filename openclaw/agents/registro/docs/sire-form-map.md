# SIRE Form Map Recon Checklist

Live SIRE browser automation is intentionally blocked until this checklist is
completed against official SIRE pages in a real recon session.

Record:

- login URL and authentication steps
- city code field source and accepted value for Owl's Watch
- Codigo de empresa value source
- entrada form fields, labels, selectors, and required/optional status
- salida form fields, labels, selectors, and required/optional status
- success receipt page fields and how to capture receipt references
- duplicate/idempotency behavior for repeated entrada or salida attempts
- failure messages for invalid document data, expired sessions, and city code issues

After recon, add a separate SIRE browser routine PR. Do not wire live SIRE
submission into the current Registro processing skill.
