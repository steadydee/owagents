# Rotate Expense Token

1. Rotate the expense intake token in Operations/Vercel.
2. Update `EXPENSE_INTAKE_API_TOKEN` in the live `owlswatch_intake` MCP runtime environment.
3. Do not write the token into this repo.
4. Restart the `owlswatch` gateway if the token is read from process environment at startup.
5. Run a receipt intake smoke test.

