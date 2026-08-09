# L2 Release Run Input Attestation

Date: 2026-08-06
Readiness: `L2_DRY_RUN_ONLY`
Roadmap slice: WP0 increments 1-4

## Problem

The immutable L2 launcher currently proves the clean Git commit that executed, but its
run receipt does not bind the logical data and evidence set consumed by that worker.
That leaves a replay gap: two runs of the same release can consume different mutable
state while producing receipts with the same release identity.

## Decision

The launcher accepts a narrow module-level `release_run_attestation()` contract.  A
worker attestation binds:

- the exact release commit;
- canonical hashes of the logical input/evidence set actually consumed;
- canonical or file hashes of the resulting authority/output state;
- the unchanged L2 safety declaration.

The launcher validates and hashes the document before embedding it in the release-run
receipt.  Modules listed as attestation-required fail closed when a successful or
safe-blocked run cannot provide a complete, valid attestation.

`alpha_production_workflow` is the first required module because it is the unique
candidate identity and evidence authority.  Its input set includes the candidate
identity set, all evidence documents, the exact attested forward-realization windows,
and the two pre-registered policy hashes.  Its output set includes the projection file
hash and both append-only authority chain heads.

`paper_multivenue_sor` is the second required module.  Its input set binds the full
canonical public cross-venue book bundle, the bundle's independently reproducible book
snapshot hash, and the fixed routing-input contract (universe, canary subset, notional,
freshness/skew limits, and fee assumptions).  Its output set binds the persisted status
file and the dry-run reproducibility hash.  A failed retry clears any prior in-memory
attestation before touching new inputs, so a previous PASS cannot attest a later run.

`realtime_alpha_execution_paper` is the third required module and the first
multi-authority consumer.  One SQLite read transaction freezes the complete Signal
Shadow event view while candidate admissions and decisions are materialized.  The run
binds that authority hash, the parsed feature and contract-filter status documents, the
execution ledger's pre-run authority hash, the fixed execution policy, and the exact
public routing snapshot when one is consumed.  Outputs bind the post-run execution
ledger authority and report file.  `SAFE_BLOCKED` remains attestable with the inputs
that were actually parsed; a new attempt always clears the previous attestation.

`binance_realtime_features` is the fourth required module and closes the first
upstream data-to-feature seam.  Its input set binds the exact verified public
WebSocket snapshot bundle, the source-chain verification view, the pre-run
feature-batch authority, the fixed materialization/drift contract, and the exact
bounded PIT feature view used for drift calibration.  Its output set binds the
append-only feature-batch authority, the append-only feature projection frontier,
and the status file.  The worker clears prior in-memory attestation before loading
any mutable authority, and the launcher refuses a successful run without the exact
paper-only safety declaration.

## Boundaries

- This is logical content attestation, not a copy of the entire mutable state root.
- It does not claim cross-host storage, WORM retention, external witnessing, or a
  signed container provenance.
- Scheduled workers other than `alpha_production_workflow`,
  `paper_multivenue_sor`, `realtime_alpha_execution_paper`, and
  `binance_realtime_features` remain explicitly
  `worker_attestation_required=false` until their real consumed-input contracts are
  instrumented in later WP0 slices.
- No lifecycle promotion, credential access, broker connectivity, order submission, or
  live-trading flag changes are introduced.
