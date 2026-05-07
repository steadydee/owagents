# Smoke Test Cotiza

Run local source checks:

```sh
./scripts/smoke-cotiza.sh
```

Manual production test:

```text
Operator quote. April 12-14, 2026. 2 guests. 1 cabin. Full board. Morning bird tour each day. Local Spanish guide. No transport.
```

Expected:

- Cotiza creates a draft quote only.
- A Google Sheet is created.
- The sheet has day sections.
- Breakfast is shown as COP 0 for cabin-stay breakfast mornings.
- Checkout-day lunch is omitted unless explicitly requested.

