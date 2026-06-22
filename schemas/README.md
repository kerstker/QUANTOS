# schemas/ — event & contract schemas

These JSON Schemas are **generated** from the canonical pydantic contracts in
[`quantos/contracts/`](../quantos/contracts) — the contracts are the single
source of truth.

- Regenerate: `python scripts/gen_schemas.py`
- Drift guard: `tests/contracts/test_schema_drift.py` fails if a committed
  schema is stale (also run by `python scripts/validate.py`).

Current schemas:
- `events/event.schema.json` — canonical event envelope (`quantos.contracts.events.Event`)
- `risk/risk_decision.schema.json` — `quantos.contracts.risk.RiskDecision`
- `trading/order_intent.schema.json` — `quantos.contracts.trading.OrderIntent`

Avro schemas (for the event bus) will be added here as the data/event
infrastructure lands (Phase 2+), kept consistent with the same contracts.
