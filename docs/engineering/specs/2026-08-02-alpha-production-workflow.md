# Alpha Production Workflow

Date: 2026-08-02
Readiness: `L2_DRY_RUN_ONLY`
Roadmap slice: WP1

## Decision

QUANTOS now has one candidate identity authority and one evidence-backed workflow up to
Execution Paper. The authority is
`services.research.alpha_production_workflow.AlphaProductionWorkflow`; its durable state is
`data/cache/alpha_production_workflow.db`, and its read-only machine projection is
`data/cache/alpha_production_workflow.json`.

The old stores are evidence sources, not competing workflow authorities:

- `PaperCandidateRegistry` proves verified Factory admission;
- immutable experiment receipts prove historical OOS evaluation;
- the attested forward ledger proves blind Signal Shadow decisions and outcomes;
- the nine-engine bundle proves candidate-specific research-risk inputs;
- the paper execution/realtime stores prove simulated fills, positions and marks;
- lifecycle evidence reports health or retirement.

## State model

Historical OOS and Signal Shadow intentionally run in parallel. A false serial state machine
would either delay forward evidence or imply that a shadow observation passed historical
validation. The canonical progression is therefore:

1. `FROZEN` — immutable candidate identity registered;
2. `PARALLEL_VALIDATION` — historical OOS and blind Signal Shadow accumulate independently;
3. `EXECUTION_PAPER_ELIGIBLE` — historical PASS, mature forward evidence, a READY nine-engine
   packet, a pinned release, and no current blocker;
4. `EXECUTION_PAPER` — an eligible candidate also has a persisted simulated paper account;
5. `RETIRED` — terminal, backed by lifecycle evidence.

Stage progression is monotonic. Current evidence can regress (for example, stale risk inputs),
which immediately makes `eligible_for_execution_paper=false` and records a new event without
erasing the historical stage. That separates "this account once entered paper" from "it is safe
to admit a new paper decision now".

The only backward correction is the explicitly audited `LIFECYCLE_FALSE_RETIREMENT_CORRECTION_V1`
migration. It is restricted to the known `2884189` parser release, where the lifecycle consumer
matched the field name `retired` instead of its boolean value, and it requires a fresh cockpit row
whose semantic lifecycle state is `RUNNING` or `BLOCKED`. Ordinary `RETIRED` records remain
terminal and cannot be reopened.

## Identity and material change

Every population row freezes expression, quantile, cadence, universe, required datasets,
economic family, hypothesis, falsification criteria, source receipt and predecessor lineage.
The registry rejects an existing candidate ID with different immutable content. A material
signal, data or governance change must create a successor identity; mixed changes are classified
`MATERIAL_MULTIPLE`. Evidence-location changes alone are non-material and cannot masquerade as a
new discovery.

Both population rows and workflow events are append-only SQLite hash chains with database
triggers rejecting UPDATE and DELETE. Exact replay is idempotent. Evidence bundles bind five
canonical gates, their content hashes and source times, the candidate identity hash and the exact
40-character release commit.

## Consumption and operation

When the workflow projection exists, it is authoritative for:

- `forward_track_advance` Signal Shadow population;
- independent forward OOS evaluation;
- candidate-generic nine-engine research-risk generation;
- paper preview, operator and realtime candidate selection;
- paper semantic health;
- the SSE-backed console Production Funnel.

The missing-file path remains a bootstrap-only compatibility mode. An existing malformed,
unsafe or unverified workflow fails closed. The scheduled reconciler runs every five minutes from
the immutable release launcher. The workflow database is a required L2 backup source under source
contract v3; the earlier Factory admission registry is retained as an optional provenance source.

## Current honest result

A read-only smoke reconciliation against the active `c90836d8aae289eabf7bb8fb6cded0dbabac98fb`
state produced a verified population of 10 candidates, zero currently eligible candidates and
zero non-legacy Execution Paper candidates. All ten have one of eight required forward periods.
The three existing marked books remain `LEGACY_DIAGNOSTIC_PAPER`; they can continue producing
public-data PnL diagnostics but cannot acquire deployment eligibility. The other seven remain in
parallel validation with historical, maturity and candidate-risk blockers recorded explicitly.

## Safety boundary

This workflow ends at simulated Execution Paper. It cannot construct a human actor, grant a live
mandate, read credentials, connect to a broker, enable live flags or submit an order. Every
projection carries `paper_only=true`, `live_allowed=false`,
`order_submission_allowed=false`, and `lifecycle_promotion=false`.
