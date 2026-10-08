# Artifact Admission V2 Review - 2026-07-15

> Historical record. Superseded on the same date by
> `2026-07-15-artifact-admission-v3-review.md`.

## Decision

`PLATFORM.SUPPLY_CHAIN` remains M3/PARTIAL and remains a top-tier red line.
This increment hardens the local, offline admission boundary; it does not prove
an externally signed QUANTOS artifact or an independently operated admission
service.

## Landed Local Controls

- Policy schema v2 with institutional upper bounds for artifact, verification,
  checkpoint, revocation, and clock-skew windows.
- Strict trust-contract types, duplicate JSON key rejection, exact signed
  payload comparison, certificate validity checks, and known-revocation
  precedence.
- Sigstore runtime version binding and observed certificate SAN/OIDC extraction.
- Explicit Rekor-v1 `integratedTime` profile; Rekor v2/TSA is not claimed.
- Repository-governed policy registry. It is empty under `L2_DRY_RUN_ONLY`, so
  no production artifact can currently receive ALLOW.
- Fixed-path SQLite decision authority with WAL/FULL durability, immutable row
  triggers, hash chaining, transaction-local clock rollback rejection, and
  commit-before-output behavior.
- The fixed authority database is an optional encrypted L2 backup source.

## Open Institutional Gaps

1. No externally identity-signed artifact or retained valid Sigstore bundle.
2. Receipt `GOOD`, checkpoint, and revocation fields are unsigned operator
   assertions; digest binding proves integrity, not external authenticity.
3. No checkpoint root/tree-size/consistency proof or signed revocation response.
4. The production fixed authority database is not provisioned.
5. Local SQLite is not WORM and has no external chain-head seal; a filesystem
   actor can replace the whole database.
6. No cross-host reproducibility and no independently deployed admission trust
   domain.
7. The actual Sigstore certificate/SAN/OIDC/SCT/Rekor path has no retained valid
   integration fixture; successful CLI tests use a verified-facts seam.

## Required Next Work

1. Obtain and retain a real workload-identity Sigstore provenance bundle.
2. Define and verify a signed receipt envelope that pins an external receipt
   authority/key and carries structured checkpoint and revocation proofs.
3. Deploy the admission service independently, provision its fixed authority,
   and externally seal each committed chain head.
4. Reproduce the immutable artifact on an independent host/builder.
5. Add Rekor-v2/TSA support only with explicit time-source semantics and tests.

No item above grants deployment, execution, credential, order-submission, or
live-trading authority. `L2_DRY_RUN_ONLY` remains unchanged.

## Verification

- Artifact admission: `80 passed`.
- Supply-chain, top-tier assessment, and backup focus: `59 passed`.
- Canonical backend: `2420 passed, 1 skipped`.
- Live/trading/tools: `972 passed`.
- Frontend: `68 passed`, typecheck and production build passed.
- Governance scanner: `313 files / 0 violations`.
- Canonical gate: all 18 stages passed.
