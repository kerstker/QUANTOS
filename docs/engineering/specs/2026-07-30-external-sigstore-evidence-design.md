# External Sigstore Evidence Design

## Goal

Obtain a genuine, externally identity-signed Sigstore attestation for an
immutable QUANTOS source artifact while preserving the L2 dry-run safety
boundary. The evidence must be produced by GitHub Actions workload identity,
record Fulcio and Rekor material, and prove that two independent hosted
builders reproduced the exact same artifact bytes.

This slice narrows the `PLATFORM.SUPPLY_CHAIN` gap. It does not claim an
independently deployed admission service, an external receipt authority,
revocation operations, WORM retention, or authority-chain sealing.

## Artifact contract

`tools/build_reproducible_source_bundle.py` builds a deterministic
`tar.gz` directly from committed Git blobs.

- paths come from `git ls-files`;
- file bytes come from `git cat-file`, never the mutable worktree;
- the commit must be a full 40-character SHA;
- tar paths are POSIX-normalized and sorted;
- uid/gid are zero and user/group names are empty;
- mode is either `0644` or `0755`, derived from the Git tree mode;
- mtime is the commit timestamp;
- gzip filename is empty and gzip mtime is the same commit timestamp;
- symlinks, submodules, non-blob entries, duplicate paths, and unsafe paths
  fail closed.

The output manifest binds the commit, archive SHA-256, per-file Git blob SHA-1,
per-file SHA-256, mode, size, and a canonical content-root digest. The archive
and manifest grant no deployment, execution, credential, broker, order, or
live-trading authority.

## External workflow

`.github/workflows/sigstore-evidence.yml` runs only when its own contract or
the reproducible builder changes, plus an explicit manual dispatch:

1. two separate jobs on `ubuntu-22.04` and `ubuntu-24.04` build the archive;
2. each job uploads the archive and manifest as a GitHub Actions artifact;
3. a third job downloads both results and requires byte equality;
4. the third job creates a SLSA provenance v1 predicate;
5. `sigstore==4.4.0` signs the archive through GitHub ambient OIDC using DSSE,
   Fulcio, SCT verification material, and Rekor v1;
6. the exact Sigstore trusted root used by the signer is retained;
7. the evidence package is uploaded as an immutable workflow artifact.

Only the attestation job receives `id-token: write`. Every job has
`contents: read`; there is no repository write, package publication,
deployment, secret, or trading permission.

## Evidence lifecycle

The first workflow run proves the external path and produces a downloadable
evidence package. A follow-up repository commit may retain that exact package
and add it to the offline artifact-admission verifier, but must preserve these
honesty labels until independently proven:

- `cross_host_reproducibility`: externally observed only for the two named
  hosted-runner images;
- `sigstore_identity`: GitHub Actions workload identity, not a human reviewer;
- `external_receipt_authority`: not provided;
- `independent_admission_boundary`: not provided;
- `revocation_operations`: not provided;
- `external_chain_head_seal`: not provided;
- `worm_retention`: not provided.

Therefore this slice alone cannot turn `PLATFORM.SUPPLY_CHAIN` to `PASS`.

## Verification

- deterministic builder unit and mutation tests;
- unsafe Git entry rejection tests;
- workflow policy tests for pinned actions, fixed runner images, least
  privilege, two-host comparison, Rekor v1, SLSA v1, and no signing secrets;
- canonical validation gate;
- a real GitHub workflow run and retained workflow artifact.
