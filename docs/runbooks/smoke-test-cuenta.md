# Smoke Test Cuenta

Run local source checks:

```sh
./scripts/smoke-cuenta.sh
```

Manual production test:

1. Send a receipt photo in the Receipts topic.
2. Confirm Cuenta replies briefly.
3. Confirm a draft appears in Operations `/expenses`.
4. Confirm the receipt photo is spooled under the Cuenta workspace.

Do not use this test to approve the expense.

