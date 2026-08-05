# L2 Release Run Input Attestation

Date: 2026-08-06
Readiness: `L2_DRY_RUN_ONLY`
Roadmap slice: WP0 first increment

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

## Boundaries

- This is logical content attestation, not a copy of the entire mutable state root.
- It does not claim cross-host storage, WORM retention, external witnessing, or a
  signed container provenance.
- Other scheduled workers remain explicitly `worker_attestation_required=false` until
  their real consumed-input contracts are instrumented in later WP0 slices.
- No lifecycle promotion, credential access, broker connectivity, order submission, or
  live-trading flag changes are introduced.
