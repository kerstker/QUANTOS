# Supply-Chain Evidence Baseline Plan

## Completed L2-Safe Work

- [x] Pin all Python dependencies used by `requirements-dev.txt`.
- [x] Pin GitHub Actions to immutable commit SHAs.
- [x] Remove the unbounded CI pip self-upgrade.
- [x] Generate a deterministic CycloneDX 1.6 SBOM.
- [x] Commit SHA-256 manifest provenance and npm integrity coverage.
- [x] Add a fail-closed M3 evidence checker and tamper tests.
- [x] Query the external npm Registry audit API, retain the canonical report,
      and bind it to the current package manifest and lockfile.
- [x] Remediate the discovered Vitest dependency chain (5 findings: 1 critical,
      1 high, 3 moderate) by pinning `vitest==4.1.10`; retained npm result is
      now zero known vulnerabilities.
- [x] Add `npm-audit-evidence:current` to the canonical validation gate.
- [x] Pin every `pyproject.toml` optional dependency and `pip-audit==2.10.1`;
      repair the `pytest 9.1.1` / `pytest-asyncio 0.23.8` resolution conflict.
- [x] Run the external Python audit, remediate 19 advisory records across
      Starlette, PyJWT, and cryptography, and retain a zero-known result bound
      to all Python manifests.
- [x] Validate the upgraded FastAPI/Starlette/PyJWT/cryptography/httpx2 stack
      through the full backend, identity, API, and console suites.
- [x] Add `python-audit-evidence:current` to the canonical validation gate.
- [x] Query OSV by immutable GitHub Action commit, retain zero-known results,
      and add `actions-osv-evidence:current` to the canonical gate.
- [x] Pin and checksum Trivy 0.72.0, reject the Debian slim base after finding
      2 critical / 18 high records, and select the digest-pinned official
      Python 3.12 Alpine amd64 manifest after comparative scans.
- [x] Build the console image as a multi-stage, non-root runtime with no pip,
      verify it under a read-only root filesystem, and retain a zero-known
      final-image result bound to the Dockerfile and fresh Trivy DB metadata.
- [x] Add `container-trivy-evidence:current` plus tamper, digest-drift,
      stale-database, severity-policy, and Dockerfile hardening tests.
- [x] Prove two same-host, same-BuildKit, no-cache OCI builds are byte-identical;
      retain the archive/manifest/layer digests and bind them to the Dockerfile,
      requirements, fixed command, and BuildKit version.
- [x] Add `container-reproducibility-evidence:current` plus OCI integrity,
      recipe-drift, archive-difference, and retained-evidence tamper tests.
- [x] Pin `sigstore==4.4.0` and add a verify-only offline admission CLI that
      requires SHA-256-pinned policy/trusted-root inputs, exact OIDC issuer and
      identity, Fulcio/SCT/Rekor/DSSE verification, and exact SLSA
      subject/builder/build-type/source policy matching.
- [x] Bind the admission implementation and its fail-closed tests into the
      supply-chain evidence checker without retaining signing capability,
      private keys, or deployment authority.
- [x] Add deterministic `generate_supply_chain_evidence.py --check` validation
      for both the CycloneDX SBOM and manifest-bound baseline, with current and
      tampered artifact tests, and run it before the top-tier assessment in the
      canonical gate.
- [x] Keep the top-tier verdict at `NOT_TOP_TIER` with the red line open.
- [x] Replace caller-declared policy hashes with an empty, repository-governed
      L2 policy registry; no production artifact can currently receive ALLOW.
- [x] Add policy v3 freshness/revocation gates, strict trust-contract parsing,
      duplicate-key rejection, runtime verifier binding, observed certificate
      identity extraction, and Rekor-v1-only transparency-time semantics.
- [x] Wire a fixed-path append-only SQLite authority so policy decisions commit
      before output; reject missing authority and clock rollback fail-closed.
- [x] Bind the policy registry, decision store, implementation hashes, tests,
      and honest external blockers into supply-chain and top-tier evidence.
- [x] Replace exact short-lived receipt hashes with stable external authority,
      key-ID, and Ed25519 public-key pins; verify domain-separated dynamic
      receipt envelopes without adding any production signing capability.
- [x] Add an empty repository-governed receipt-authority registry and prove that
      dynamic signed receipts rotate without policy or policy-registry churn.
- [x] Upgrade both registries to schema v2 monotonic generations, reread them
      after Sigstore verification, and deny status/digest/generation drift.
- [x] Reject registry generation rollback and same-generation digest conflict
      in the append-only authority transaction; require complete authority and
      signed-receipt binding for every `ALLOW`.
- [x] Add policy/key revocation-during-verification and registry-history threat
      regression tests.

## Remaining M4 Work

- [ ] Prove reproducibility on an independent host/builder; the current result
      is deliberately limited to two cold builds on one Docker Desktop host.
- [ ] Establish external workload identity for artifact signing.
- [ ] Produce and retain a real identity-backed Sigstore provenance bundle.
- [ ] Deploy the existing verifier at an independent admission boundary and
      retain its real allow/deny evidence.
- [ ] Add freshness, revocation, and missed-run alerting for signed evidence.
- [ ] Provision a real external receipt authority and retain independently
      issued signed receipts; the production authority registry remains empty.
- [ ] Verify source-native checkpoint root/tree-size/consistency and signed
      revocation response proofs rather than trusting only authority assertions.
- [ ] Provision the fixed authority only at an independently deployed boundary
      and externally seal its chain head; the local SQLite file is not WORM.
- [ ] Retain a valid Sigstore 4.4 Rekor-v1 fixture or external bundle and run the
      actual certificate/SAN/OIDC/SCT/Rekor integration path in tests.
- [ ] Move registry and decision history into one independently deployed,
      externally sealed compare-and-swap authority; local files plus SQLite do
      not provide cross-resource atomicity or survive coordinated replacement.

## Verification

```bash
python scripts/generate_supply_chain_evidence.py
python scripts/generate_supply_chain_evidence.py --check
python scripts/generate_container_audit_evidence.py --check
python scripts/generate_container_reproducibility_evidence.py --check
python scripts/assess_top_tier.py --check
python -m pytest -q tests/governance/test_container_audit_evidence.py \
  tests/governance/test_container_reproducibility_evidence.py \
  tests/governance/test_artifact_admission.py \
  tests/governance/test_artifact_receipt_auth.py \
  tests/governance/test_artifact_admission_cli.py \
  tests/governance/test_artifact_admission_store.py \
  tests/governance/test_supply_chain_evidence.py \
  tests/governance/test_top_tier_assessment.py
python scripts/validate.py
```

Latest verification (2026-07-15): artifact-admission core/store/CLI suite
`102 passed`; admission/receipt/supply-chain/top-tier focused suite `165
passed`; canonical backend gate `2456 passed, 1 skipped`; live stack `972
passed`; frontend `68 passed` plus typecheck and production build; governance
scanner `314 files / 0 violations`.
The machine assessment has `15 PASS / 0 FAILED / 5 PARTIAL / 2
manual-external`; supply chain is honestly M3/PARTIAL.

M4 completion is prohibited until independent-host reproducibility and external
identity evidence exist. Local files, self-signed keys, or manually edited
scorecards are not substitutes.
