# Paper Research-Risk Attestation Scope Correction

**Status:** implemented; L2 paper/research only; no admission, risk, execution,
or live-trading gate is weakened.

## 1. Incident

The DGX `paper_research_risk_bundle` worker built a valid `PENDING` document but
its release attestation failed. The worker hashed every mutable source before
and after the build, including the full production experiment registry. That
registry is roughly 12 GB and append-only. Two full reads extended one build to
several minutes, so unrelated registry appends or normal forward-data refreshes
could change the broad source view before publication.

The failure was fail-closed and preserved the last attested artifact. It was an
attestation-scope and liveness defect, not evidence that an Alpha passed its
research-risk gates.

## 2. Correct authority boundary

The risk builder does not consume the whole experiment registry. For each
referenced evaluation it calls `ExperimentRegistry.verify_evaluation(run_id)`
and then consumes that immutable record's run id, content hash, dataset version,
and evaluation payload.

The release proof now:

1. keeps pre/post byte hashes and exact equality for all file authorities that
   are actually consumed as files;
2. copies each consumed registry record's `evaluation_run_id` and
   `evaluation_content_hash` into the Alpha metadata;
3. binds the sorted consumed-record view as
   `input_hashes.verified_evaluation_records`;
4. rejects any referenced record with a missing run id or malformed SHA-256;
5. continues to bind the complete candidate set, risk contract, output bundle,
   release commit, and paper-only safety contract.

The unrelated bytes of the multi-gigabyte append-only registry are no longer
part of the worker transaction. An append for a different experiment therefore
cannot invalidate proof for the records actually used in this bundle.

## 3. Preserved failure behavior

- A registry record that fails its native hash-chain verification is not
  admitted by the builder.
- A malformed consumed evaluation identity makes the release attestation
  incomplete.
- Any byte authority that changes during the build still makes the release
  attestation incomplete.
- An incomplete attestation does not replace the last published risk bundle and
  remains a failed scheduled receipt.
- `PENDING` remains a valid, explicit research state; it does not grant order or
  execution authority.

## 4. Verification

Tests pin exact consumed-record binding, malformed-hash rejection, exclusion of
unrelated registry bytes, source-race rejection, atomic non-publication, and
scheduled-entrypoint attestation validation.
