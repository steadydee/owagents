# Owl's Watch Payroll Tools

The Nómina service owns deterministic semimonthly payroll, immutable finalized
snapshots and an append-only employee-loan ledger. It is the local v1 authority;
Operations integration is deferred. No tool executes a bank transfer.

`catalog.py` is the single model-tool contract. The native OpenClaw plugin reads
`server.py catalog` and derives registrations; do not hand-maintain a second
schema or enable an additional MCP transport.

The package separates calculation, transactional storage, workflow engine,
authorization, private reporting and optional encrypted archive delivery. Amounts
are integer COP. Pure calculations and behavior are exercised with synthetic
fixtures, not screenshots containing personal data.

## Runtime

Pin dependencies using `requirements.txt` in a private `.venv`. Set
`OWLSWATCH_PAYROLL_WORKSPACE` to the private root (mode `700`) and optionally
`OWLSWATCH_PAYROLL_PYTHON` for the plugin's interpreter. Runtime configuration
defaults to `payroll-config.json` inside that root, mode `600`; an explicit
`OWLSWATCH_PAYROLL_CONFIG` must still stay inside it. Use the sanitized profile
examples under `openclaw/profiles/nomina`; do not commit the live files.

All model calls require host Telegram identity and local sender/route checks.
The model has draft-editing and preparation tools only. Permanent changes use
an unexpired exact preparation, authenticated native `/revisar_nomina TOKEN`,
and the `/confirmar_nomina TOKEN` supplied by that review. Native review renders
the complete saved action and persists the review marker. Both commands bind
sender/account/chat/topic/session; confirmation checks review/current business
state and records the transition atomically.

Finalization reserves loan amounts; selected-payee payment confirmation posts
repayments. An override of zero skips once without catch-up. Corrections preserve
history through supersession or compensating events. Keep all state, finalized
snapshots, exports, archive outbox, backups and secrets across source deployments.

Private exports contain JSON, CSV and printable HTML. Full payment destinations
are limited to authenticated native review and private artifacts; ordinary model
results mask them. Optional Drive
archiving uses a fixed private folder with encrypted SQLite online-backup copies.
Keys/configuration are excluded from those copies and require separate secure
retention. Archive retries cannot replay a payroll commit.

## Verification

```sh
./scripts/smoke-nomina.sh
python3 tools/owlswatch_payroll/server.py catalog
python3 -m unittest discover -s tools/owlswatch_payroll/tests -p 'test_*.py' -v
```

The catalog command needs no live configuration and performs no payroll write.
The smoke selects a temporary synthetic workspace and invokes native plugin tests
as well as Python tests. Actual bot/context acceptance and optional Drive/restore
verification are separate deployment checks described in `docs/nomina-setup.md`.

The installed SDK lacks a trusted Telegram message ID in tool context. Preserve
request IDs for intended edits/retries; host tool-call IDs are audit evidence, not
a guarantee of deduplicating every Telegram redelivery. No live baseline, payroll
or loan balance is included in this package.
