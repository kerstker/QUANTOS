# Artifact Admission V3 Review - 2026-07-15

> Superseded for local atomicity by
> `2026-07-15-artifact-admission-v4-review.md`. The filesystem reread described
> below did not close a revocation committed after reread and before decision
> record; V4 adds one ordered registry/decision authority and exact-head CAS.

## Decision

`PLATFORM.SUPPLY_CHAIN` remains M3/PARTIAL and a top-tier red line. Policy v3
removes the unstable exact hash of a short-lived receipt and pins a stable
external authority ID, key ID, and Ed25519 public-key digest instead.

## Landed Local Controls

- Domain-separated canonical signed-receipt payload and strict, unpadded
  base64url envelope contract.
- Verify-only Ed25519 path. Production code imports no private key or signer.
- Repository-governed receipt-authority key registry with ACTIVE/REVOKED state.
- Dynamic signed receipts rotate without policy or policy-registry changes.
- Core policy evaluation independently binds verified authority facts, receipt
  digest, envelope digest, policy, authority registry, artifact, bundle, root,
  certificate, verifier runtime, and the admission clock.
- The append-only store rejects ALLOW unless authenticated receipt evidence is
  present, and commits before the CLI emits success.

## Current Production Reality

- The policy registry is empty.
- The receipt-authority registry is empty.
- The fixed production admission database is not provisioned.
- Therefore no real artifact can receive ALLOW.

## Open Institutional Gaps

1. No real external receipt authority or externally signed receipt is retained.
2. No source-native checkpoint root/tree-size/consistency proof is verified.
3. No signed OCSP/CRL-style revocation response is retained and verified.
4. No valid Sigstore bundle exercises the actual SAN/OIDC/SCT/Rekor path.
5. Rekor v2/TSA is unsupported; only Rekor v1 integrated time is modeled.
6. Local SQLite is replaceable, not WORM, and has no external chain-head seal.
7. No cross-host reproducibility or independently deployed admission service.

## Post-Review TOCTOU Closure

The independent threat review found that an ACTIVE policy or receipt key could
be revoked while the comparatively slow Sigstore verification was running,
leaving the original snapshot eligible for a stale `ALLOW`. The local boundary
now closes that observed race as follows:

- both registries use schema v2 with a non-negative monotonic `generation`;
- the CLI rereads both registries after Sigstore verification and denies when
  status, exact file digest, or generation differs from the preflight snapshot;
- the append-only authority rejects a generation below retained history and
  rejects different registry digests at the same generation inside the same
  `BEGIN IMMEDIATE` transaction;
- an `ALLOW` must bind its admission time, authority kind/schema/decision ID,
  both registry generations, and complete authenticated-receipt fields;
- regression tests revoke each registry during mocked Sigstore verification
  and prove that no `ALLOW` is committed.

This is a local fail-closed improvement, not a distributed atomicity claim.
Repository registry files and the SQLite authority are not one transactional
system, and a filesystem actor can still replace the registry and the complete
unsealed database together. A real M4 boundary still requires an independently
deployed registry/admission authority with external history sealing or an
equivalent compare-and-swap protocol.

Verification after the closure: admission core/store/CLI `102 passed`; the
combined admission/receipt/supply-chain/top-tier set `165 passed`; the canonical
18-stage gate passed with backend `2456 passed / 1 skipped`, live/trading/tools
`972 passed`, frontend `68 passed` plus typecheck/build, and governance scan
`314 files / 0 violations`.

No deployment, execution, credential, order-submission, or live-trading
authority is granted. `L2_DRY_RUN_ONLY` remains unchanged.
