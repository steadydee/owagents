# Gmail And Drive Access

Cotiza uses Google credentials at runtime to:

- read Owl's Watch Gmail quote requests
- create Google Drive quote sheets

Recommended scopes:

- Gmail: `https://www.googleapis.com/auth/gmail.readonly`
- Drive/Sheets for quote sheet creation: Drive and Sheets scopes in the tool runtime only

Do not commit:

- service account JSON
- OAuth token caches
- raw Gmail thread exports
- generated quote sheets

Share the configured quote folder with the service account if the service account creates sheets.

