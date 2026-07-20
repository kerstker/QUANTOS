# Catalog-bound PBO evidence

**Status:** approved for L2 research implementation, 2026-07-20.

## Problem

`AlphaEvaluator.evaluate()` currently accepts `pbo=<float>`. The autonomous factory computes that
number with CSCV, but the evaluator and immutable evaluation artifact cannot distinguish that result
from a caller-written `0.0`. Trial count is already catalog-owned and BHY already drives the
`VALIDATED` transition; the remaining Rank #12 trust gap is the unbound PBO scalar.

## Required outcome

PBO becomes a versioned, replayable batch-evidence object rather than a scalar assertion:

- retain the ordered candidate definition hashes, alignment mode, aligned-period count,
  aligned-calendar digest and the exact block-by-candidate performance matrix;
- recompute CSCV PBO from that matrix at every trust boundary;
- bind the evidence object and its digest into `AlphaEvaluation`, its reproducibility hash and the
  immutable experiment artifact;
- bind the evaluation to the catalog's complete trial-set digest, not only its integer count;
- require valid PBO evidence and the current trial-set digest for `VALIDATED` and `PAPER_TRADING`;
- allow unassessed/single-candidate evaluations to be recorded, but never treat them as
  promotion-eligible;
- expose only hashes/counts/alignment metadata in the read-only console, not the retained matrix.

## Evidence contract

`BatchPboEvidence` is frozen and versioned. Its matrix is limited to the existing CSCV maximum of
eight blocks, contains at least two distinct candidate definitions, is finite and rectangular, and
has an explicit alignment mode:

- `EVENT_TIME_INTERSECTION`: every candidate was aligned on the same calendar intersection and the
  sorted common timestamps are digest-bound;
- `POSITIONAL_LEGACY`: retained only for untimestamped legacy research inputs and labelled as such.

The shared verifier recomputes `pbo_cscv(performance_matrix)` and rejects structural, subject,
digest or numeric drift. No second PBO implementation is introduced.

## Runtime flow

1. Factory/agent loop register every candidate trial before any backtest.
2. The batch builder runs the existing panel backtest, performs the existing calendar alignment,
   forms the CSCV block matrix, and creates verified evidence.
3. `AlphaEvaluator` accepts only `pbo_evidence`; a raw `pbo` keyword is no longer part of its API.
4. The evaluator verifies the evidence and candidate membership, derives the scalar PBO, and stamps
   the catalog trial-set digest.
5. Lifecycle guards independently verify the evidence and compare the retained trial-set digest
   with the current catalog before advancing.
6. Experiment verification parses the stored `AlphaEvaluation` and replays the PBO verifier after
   checking the immutable content hash.

## Fail-closed cases

- missing evidence, single candidate, insufficient overlap or malformed panels;
- duplicate candidate definition hashes;
- non-finite/ragged/oversized matrices;
- recomputed PBO mismatch, evidence-hash mismatch or candidate-subject mismatch;
- current catalog count growth or same-count trial-set replacement;
- legacy evaluation artifacts without bound PBO evidence when used for promotion.

## Safety boundary

This changes research evidence and lifecycle permission only. It adds no network, credential,
broker, order, deployment or live-trading capability. It does not defend against trusted code that
rewrites the whole input matrix before first commit; that remains inside the documented single-
process trusted-code boundary and is governed by review, immutable storage and future process
isolation.
