# Canonical realtime candidate handoff

Status: implemented and runtime-verified for `FROZEN_V1` WP1/WP3  
Boundary: `L2_DRY_RUN_ONLY`

## Problem

The autonomous realtime factory currently owns 26 immutable registrations and
an engineering-only Signal/Execution Paper loop, while the canonical
`AlphaProductionWorkflow` owns a different population of 10 daily candidates.
This leaves two identities and two funnels.  In addition, the realtime
registration binds the mechanism and horizon but the published discovery
artifact does not retain the complete evaluation policy (decision interval,
latency, costs, quantile and maturity spans) as one content-addressed object.

## Decision

1. Publish a canonical `realtime-alpha-research-policy.v1` object and hash from
   every discovery cycle.
2. Add an explicit `REALTIME_MECHANISM` candidate-definition kind to the single
   workflow authority.  Existing `ALPHA_EXPRESSION` identity payloads and
   hashes remain byte-for-byte unchanged.
3. Build realtime identities only from a verified append-only family registry
   plus a matching discovery manifest and policy hash.  The frozen signal spec
   binds registration record hash, mechanism, required features, decision
   interval, label horizon, latency, quantile, costs, label policy and adaptive
   lineage.
4. Register the realtime population idempotently in
   `AlphaProductionWorkflow`.  A changed frozen spec under the same candidate
   ID fails closed and therefore requires a new candidate identity.
5. Map the discovery historical verdict into the canonical historical gate.
   The existing engineering shadow and simulated execution ledgers are retained
   as observable evidence but explicitly receive no formal gate credit.

## Safety invariants

- Handoff cannot set `eligible_for_execution_paper`, promote lifecycle state,
  access credentials, connect to a broker or submit an order.
- `ENGINEERING_SHADOW_REALIZED` remains feedback data only.  It cannot satisfy
  canonical `SIGNAL_SHADOW=MATURE` because it has been continuously visible to
  the adaptive selector.
- Missing, malformed, mismatched or hash-invalid registry/discovery evidence
  blocks reconciliation; it is never silently skipped once either realtime
  authority exists.
- Adaptive children retain their predecessor, mutation, generation and
  `feedback_cutoff_ms`; the current challenger changes signal plus falsification
  policy, so it is classified `MATERIAL_MULTIPLE` and restarts formal evidence.
- Existing daily candidates retain their historical identity and definition
  hashes, avoiding an artificial evidence-clock reset.

## Acceptance evidence

- Existing 10-candidate workflow identities replay unchanged.
- A valid 26-candidate realtime registry/report produces 26 additional frozen
  canonical identities and a population of 36 in the current L2 state.
- Registry-chain, manifest, policy, config, lineage and safety tampering all
  fail before canonical registration.
- Every handed-off realtime candidate remains non-eligible with an explicit
  blinded-shadow blocker until a separate formal shadow authority exists.
- Scheduler, backup, API/SSE and console report one canonical population and
  retain all paper/live safety flags as false.

Runtime acceptance on 2026-08-04 produced `population=36`,
`realtime_handoff_count=26`, `execution_paper_eligible=0`, with all realtime
candidates in `PARALLEL_VALIDATION`.  The API projection returned the same 36
rows and grouped formal blockers into the operator exception queue.
