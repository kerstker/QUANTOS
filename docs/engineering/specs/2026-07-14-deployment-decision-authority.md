# Deployment Decision Authority (2026-07-14)

## Scope

This increment makes deployment **evaluation** durable and recoverable at L2. It
does not implement a deployment executor, service rollout, broker connection, or
live-trading activation.

## State Model

Only two states exist:

- `DENIED`
- `ELIGIBLE_NOT_APPLIED`

There is deliberately no `DEPLOYED` or `APPLIED` state. An allowed PAPER decision
means only that policy evaluation succeeded; it does not mutate an environment.
LIVE remains denied by the platform readiness pin and disabled safety flags.

## Authority

`DeploymentDecisionStore` retains the full decision and its canonical v1.1 event
in one `BEGIN IMMEDIATE` transaction. Evaluation and dispatch histories are
separate append-only SHA-256 chains protected by UPDATE/DELETE triggers.

The stored decision binds environment, subject, actor, result, reasons, version
IDs, evaluation time, and approval/grant causation. Event IDs also bind the full
decision content, so distinct decisions in the same millisecond do not collide.
Exact replay is idempotent; conflicting replay and per-subject clock rollback are
rejected.

## Supported Entry Point

`evaluate_and_record_deployment` is the only production caller of the pure
`evaluate_deployment` policy function. It first evaluates policy, then writes the
authority. If the authority cannot commit, an otherwise allowed decision becomes
`DEPLOYMENT_STATE_ERROR` and is denied.

Direct event publication is not part of this entry point. The committed outbox is
drained by `dispatch_deployment_event_outbox`; `tools/deployment_events.py` then
advances the shared idempotent projection. Publish-before-ACK crashes replay
safely. The console exposes only whitelisted read-only event fields.

## Honest Boundary

The approval store, deployment evaluation store, and other audit stores are not a
distributed transaction. Actual rollout state, external identity, signed release
authorization, staging/production isolation, and cross-host HA remain open. The
drain tool cannot submit orders, connect to a broker, or apply a deployment.

## Verification

- `tests/deployment/test_deployment_store.py`
- `tests/tools/test_deployment_events.py`
- `tests/governance/test_event_bus_wiring.py`
- `tests/research/test_console_event_projection.py`
- `tests/governance/test_event_schema_catalog.py`
- `tests/governance/test_supply_chain_evidence.py`

The focused deployment/event/console/governance set passes 209 tests. Ruff,
service graph, Schema Catalog, and supply-chain drift checks are green. The honest
machine verdict remains `NOT_TOP_TIER` at 16/22 PASS. The final unified gate
passes 2305 main tests with 1 skip, 917 live/trading/tools tests, 68 frontend
tests plus typecheck/build, and 308 governance-scanned files with zero violations.
