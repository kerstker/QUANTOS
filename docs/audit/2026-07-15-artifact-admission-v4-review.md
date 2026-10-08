# Artifact Admission V4 Review - 2026-07-15

## Decision

`PLATFORM.SUPPLY_CHAIN` remains M3/PARTIAL and a top-tier red line. V4
closes the remaining observed local stale-`ALLOW` race by ordering exact
registry snapshots and admission decisions in one SQLite authority chain and
requiring transaction-time compare-and-swap against both registry heads.

This review supersedes the local TOCTOU-closure claim in
`2026-07-15-artifact-admission-v3-review.md`. The V3 filesystem reread was
necessary but not sufficient: a registry revocation could still commit after
the reread and before the decision transaction.

## Landed Local Controls

- Decision contract schema v4 binds policy and receipt-authority registry
  generation, digest, authority event sequence, and authority event hash.
- Store schema v4 places both registry snapshots and admission decisions on
  one append-only, hash-chained, totally ordered authority event sequence.
- Registry generation starts at zero and advances exactly by one. Same-version
  byte drift, rollback, gaps, and stale CAS heads fail closed.
- An `ALLOW` must match the exact current policy and receipt-authority heads
  inside the same `BEGIN IMMEDIATE` transaction that records the decision.
- The verifier uses store-retained exact bytes as runtime registry authority.
  Repository JSON files are explicit provisioning mirrors, never an automatic
  admission-time provisioning source.
- If a head changes after evaluation but before record, the stale `ALLOW` is
  discarded and a `DENY` is committed after the revocation event. The CLI never
  emits an unpersisted `ALLOW`.
- Cross-connection reads verify all authority tables from one SQLite read
  snapshot. Exact decision replay remains idempotent after later events.
- Registry and decision writes reject timestamps more than five minutes ahead
  of the local authority clock. A rollback attempt is retained as a logical-time
  `DENY` when the authority remains writable; truly unavailable output is
  explicitly labeled `UNRECORDED`.

## Verification

Focused admission core/store/CLI tests: `121 passed`. The regression suite
includes policy and receipt-key revocation after evaluation but before record,
consistent reads during a cross-connection commit, future-clock rejection,
rollback-DENY persistence, and replay after a later event. It proves that the
revocation event precedes the retained `DENY` while no `ALLOW` exists.

Full repository validation is pending the evidence regeneration performed by
this wave and must be recorded separately after it passes.

The subsequent restore-history wave ran the complete gate: the main suite
reported `2571 passed / 1 skipped` plus exactly two tracked-artifact drift
failures (service import graph and supply-chain baseline), live/trading/tools
reported `986 passed`, the frontend reported `68 passed` with typecheck/build,
and the governance scanner reported `318 files / 0 violations`. The
top-tier assessment artifact also remains drifted. This is strong
implementation regression evidence, but it is not a green unified gate; V4
evidence regeneration is still required.

## Current Production Reality

- Both repository mirrors are generation zero and empty.
- The fixed production admission database is not provisioned.
- No real artifact can receive `ALLOW`.
- L2 dry-run restrictions and all no-live-trading safety pins are unchanged.

## Open Institutional Gaps

1. No valid external Sigstore bundle exercises the real verifier path.
2. No external receipt authority or externally signed receipt is retained.
3. No source-native checkpoint consistency or certificate revocation proof is
   independently verified.
4. The authority is a replaceable local SQLite file, not WORM, externally
   sealed, replicated, or protected by a separate trust domain.
5. No cross-host reproducibility or independently deployed admission service
   is proven.
6. Rekor v2 and TSA evidence remain unsupported.
7. The future-skew guard uses a process-monotonic clock anchored to the local
   wall clock at startup; no external trusted time authority or signed time
   evidence is available.

The correct evidence label is
`LOCAL_SINGLE_SQLITE_LINEARIZABLE_REGISTRY_DECISION_CAS_UNSEALED`. It is a
meaningful local atomicity improvement, not an M4 distributed assurance claim.
Repository mirrors are non-authoritative provisioning inputs. An observed
mismatch fails closed, but their filesystem state is not part of the SQLite
commit and must not be described as a transactionally bound security head.
