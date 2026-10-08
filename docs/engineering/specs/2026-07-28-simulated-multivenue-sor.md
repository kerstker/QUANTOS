# Simulated multi-venue SOR — L2 design

Date: 2026-07-28  
Capability: `EXECUTION.MULTIVENUE.SOR`  
Scope: research, paper and dry-run planning only

## Purpose

QUANTOS previously had exchange adapters and cross-exchange data channels but no
venue-neutral smart-order-routing primitive.  This design closes that local
engineering gap without claiming production execution evidence.

`services/execution/simulated_sor.py` accepts inert `OrderIntent` values and
caller-supplied order-book snapshots.  It returns a deterministic
`SimulatedRoutePlan`; it performs no network I/O and has no submission method.

## Routing objective

Each executable depth level receives a side-aware all-in price:

- BUY: ask + taker fee + configured latency-risk penalty;
- SELL: bid - taker fee - configured latency-risk penalty.

Candidates are ordered by all-in price, then raw price, venue ID and level
index.  This explicit tie-break makes input order irrelevant.  Allocation walks
the ranked depth while enforcing:

- quote freshness;
- raw limit-price protection;
- per-venue concentration cap;
- available level depth;
- explicit minimum fill ratio.

If the minimum ratio cannot be met, the plan is rejected atomically and returns
no slices.  Partial routing therefore requires an explicit policy below 100%.

## Fail-closed conditions

Malformed prices or quantities, crossed/locked books, non-monotonic depth,
duplicate venue IDs and future-dated snapshots are rejected.  Stale and
non-executable books are retained as deterministic exclusion reasons.

All money arithmetic uses `Decimal`.  Wall clock, randomness, credentials,
venue SDKs and network imports are absent.

## Safety boundary

The primitive is simulation-only and does not alter the L2 safety configuration.
It is not wired to `live/pipeline.py` and cannot turn a plan into a live order.
Existing pre-trade gates, mandates, kill-switches and live-adapter construction
guards remain authoritative.

The plan is now load-bearing in `services/execution/dry_run_pipeline.py`.
Single-period and multi-period dry-runs accept an optional route planner plus a
mandatory `routing_snapshot_id`, which becomes part of the immutable run ID.
Every plan is independently consistency-checked before consumption; a planner
exception, rejected route, identity/quantity mismatch, non-simulation plan,
tampered slice identity or totals causes zero fills for that intent.

Accepted route slices are submitted separately to the existing dry-run adapter.
Raw venue price becomes the paper fill price; venue fee and latency penalty are
charged in addition to the existing flat-bps or impact cost, then flow through
the normal reconciliation identity.  Exact route summaries and redacted
rejection reasons are retained in `DryRunRun.detail`.  The default no-SOR path
remains behavior-compatible.

`services/execution/simulated_tca.py` now turns every accepted route into a
route-level TCA record.  The record binds the reference price, planned and
realized raw/all-in VWAP, expected and realized venue costs, requested/routed/
filled/residual quantities, side-aware implementation shortfall, and each
venue's quantity/notional/cost contribution.  A child rejected by the dry-run
adapter remains visible as execution residual rather than being silently
removed.

Every TCA record carries a canonical SHA-256 digest.  `DryRunStore` verifies the
digest, arithmetic identities, route identity/status and period envelope both
before append and when the database is reopened.  New routed runs declare
`route_tca_schema_version=1`; the immutable run snapshot and canonical
completion event bind the complete detail through
`DryRunRun.reproducibility_hash`.  Missing or non-positive TCA reference prices
produce zero fills for that intent.

`services/execution/paper_tca_history.py` adds the missing longitudinal,
read-only projection over those immutable records.  It verifies every retained
TCA record before producing timestamped route points and notional-weighted
venue statistics.  One timestamp is honestly labelled `SINGLE_SNAPSHOT`;
`LONGITUDINAL_READY` requires at least two distinct retained run timestamps.
The dry-run console surfaces this projection and displays `NO_ROUTE_TCA` rather
than treating ordinary single-venue dry-runs as multi-venue evidence.

## Verification

`tests/execution/test_simulated_sor.py` covers fee-aware and latency-aware
ranking, BUY/SELL objectives, multi-level splitting, venue caps, stale quote
exclusion, limit prices, atomic insufficient-liquidity rejection, explicit
partial routing, deterministic tie-breaking and malformed/future input denial.
`tests/execution/test_execution_import_boundary.py` continues to prohibit broker
SDK imports throughout `services/execution`.

`tests/execution/test_dry_run_pipeline.py` additionally proves split fills,
route-cost PnL, route-plan tamper denial before adapter use, rejected/error
planners producing zero fills, snapshot-bound run identity, multi-period routing
and default-path compatibility.  `tests/execution/test_simulated_tca.py` proves
BUY/SELL shortfall direction, venue reconciliation, deterministic records,
adapter-rejection residuals, exact plan-slice binding and tamper rejection.
`tests/execution/test_dry_run_store.py` proves save/reopen verification and
detects retained TCA mutation after a trigger is removed.
`tests/execution/test_paper_tca_history.py` proves deterministic longitudinal
aggregation, venue attribution, honest single-snapshot status, non-routed-run
exclusion and fail-closed handling of tampered retained evidence.

The top-tier assessment reports this as M3/PARTIAL local readiness.  It is
structurally impossible for the local readiness checker to produce PASS for a
`POST_L2` requirement.

## Remaining M4 evidence

Production maturity still requires post-L2 approval plus independently retained
evidence from real venues: authenticated broker connectivity, real fill quality,
venue outage/failover behavior, reject/retry semantics, clock synchronization,
production TCA and operational supervision.  None is inferred from simulation.
