# Supply-Chain Evidence Baseline Design

## Goal

Establish a deterministic, machine-verifiable L2-safe software supply-chain
baseline without claiming that offline repository evidence is equivalent to an
externally signed production artifact.

## Scope

The baseline covers:

- exact Python direct requirements used by the canonical validation gate;
- the npm v3 lockfile and its package integrity hashes;
- immutable commit-SHA pins for every GitHub Action;
- deterministic CycloneDX 1.6 component inventory;
- SHA-256 commitments for every manifest consumed by the generator;
- proof that the governance scanner remains wired into the canonical gate;
- retained external npm, Python, Actions, and container vulnerability results;
- byte-identical same-builder OCI reproducibility evidence;
- a verify-only offline Sigstore admission boundary for DSSE/SLSA provenance.

It does not cover a QUANTOS artifact signed by an external workload identity,
independent-host reproducibility, deployment of the verifier in a separate trust
domain, or operational freshness/revocation evidence.

## Artifacts

- `scripts/generate_supply_chain_evidence.py`
- `evidence/sbom.cdx.json`
- `evidence/supply_chain_baseline.json`
- `services/governance/top_tier_assessment.py`
- `services/governance/artifact_admission.py`
- `tools/verify_artifact_admission.py`
- `tests/governance/test_artifact_admission.py`
- `tests/governance/test_artifact_admission_cli.py`
- `tests/governance/test_supply_chain_evidence.py`

The generator contains no wall clock, randomness, network access, secret access,
or machine-specific paths. Identical repository inputs produce byte-identical
JSON documents.

## Evidence Semantics

The evidence verdict is `PARTIAL`. The assessor may grant M3 only when it can
recompute the SBOM digest and every manifest digest, all direct gate requirements
are exact-pinned, the npm lockfile is version 3, and every CI Action uses a full
40-character commit SHA.

The evidence must continue to state that:

- external advisory evidence is retained and current;
- `artifact_signature_status=VERIFIER_READY_EXTERNAL_SIGNATURE_MISSING`;
- the verifier contains no signing or private-key path;
- an external bundle and independent deployment are not provided.

The assessor recomputes both verifier source digests, requires exact dependency
pins and verification tests, and scans for the load-bearing Sigstore/identity/
policy markers and forbidden signing paths. Because `PLATFORM.SUPPLY_CHAIN`
targets M4, M3 remains `PARTIAL` and the red line stays open.

## M4 Exit Conditions

1. Reproduce the immutable artifact on an independent host/builder.
2. Sign provenance through an external workload identity, not a repository key.
3. Retain the real Sigstore bundle and pinned trust-root/policy evidence.
4. Run the verifier and enforce its result at an independent admission
   boundary.
5. Add freshness, revocation, and missed-run monitoring to the canonical gate.

## 2026-07-15 Admission V3 Addendum

The local admission boundary now uses policy schema v3. It rejects duplicate
JSON object keys, non-strict trust-contract types, signed-payload whitespace
normalization, unbounded freshness windows, verifier-runtime drift, stale or
future evidence, certificate validity mismatches, known revocation, and wall
clock rollback. The supported transparency-time profile is deliberately
limited to Sigstore 4.4 Rekor v1 `integratedTime`; Rekor v2 and TSA bundles are
not claimed.

`config/artifact_admission_policy_registry.json` is the repository-governed
policy authority. Policy v3 pins a stable external receipt authority, key ID,
and Ed25519 public-key digest rather than one short-lived receipt digest.
`config/artifact_receipt_authority_registry.json` governs verify-only public
keys. Both registries are intentionally empty under `L2_DRY_RUN_ONLY`, so the
real repository admits no artifact. `ArtifactAdmissionStore` provides a fixed
path, append-only, hash-chained SQLite decision authority, and the CLI commits
before emitting an `ALLOW`. The production database is intentionally not
provisioned, so this is a wired fail-closed code path rather than an operational
admission service.

The dynamic receipt is now carried in a domain-separated Ed25519 envelope and
can rotate without policy-registry churn. Production code only verifies public
keys; it contains no signing or private-key path. No real external authority or
signed receipt is provided, and source-native checkpoint root/tree-size/
consistency and signed revocation proofs are still not verified. The local
SQLite file is not WORM and can be replaced wholesale by a filesystem actor;
no external chain-head seal exists. No retained valid Sigstore bundle exercises
the real certificate/SCT/Rekor path. These limitations keep
`PLATFORM.SUPPLY_CHAIN` at M3/PARTIAL and its red line open.

Both repository registries now use schema v2 and carry monotonic generations.
Admission rereads both exact registry snapshots after Sigstore verification;
status, digest, or generation drift produces a dedicated denial. The local
decision authority also rejects generation rollback and same-generation digest
conflicts under its write transaction, and requires every `ALLOW` to bind the
authority clock, store identity, decision ID, registry generations, and signed
receipt fields. This prevents the reviewed long-running verification race while
retained local history is intact. It does not make two files and SQLite one
atomic distributed authority, nor does it survive coordinated replacement of
the unsealed registry and database.
