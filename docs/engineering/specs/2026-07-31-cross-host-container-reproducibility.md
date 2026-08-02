# Cross-host deployable-container reproducibility

## Decision

QUANTOS retains the existing same-host, two-cold-build OCI evidence as a local
fail-closed gate. A separate external workflow now asks two distinct GitHub
hosted runner images (`ubuntu-22.04` and `ubuntu-24.04`) to build the deployable
read-only console image.

Both jobs use:

- the same digest-pinned application base image;
- the same full-SHA-pinned `docker/setup-buildx-action`;
- the same digest-pinned BuildKit daemon image;
- no cache, no provenance injection, `linux/amd64`, and timestamp rewriting;
- the repository's fixed `SOURCE_DATE_EPOCH`.

Each runner locally parses the complete OCI archive before emitting a compact
claim. The claim binds the source commit, runner/job identity, recipe files,
the complete byte content of every file under `services/` and `quantos/`,
toolchain, archive bytes, manifest, config, compressed layers, uncompressed
diff IDs, and rewritten timestamps. The complete build-input set is represented
by a deterministic content root; claims include that root and a canonical claim
hash.

A third job has GitHub OIDC permission. It accepts exactly one claim from each
approved runner, validates both against the checked-out recipe, and requires
the archive SHA-256, size, image graph and toolchain to be identical. It signs
the aggregate JSON with Sigstore/Fulcio/Rekor and uploads the proof, bundle and
receipt with 90-day GitHub artifact retention.

## Trust boundary

This proves reproduction across two independently provisioned hosted runner
instances and two operating-system images. It does not prove:

- organizational independence from GitHub;
- an independent admission or revocation authority;
- authenticated receipt storage outside GitHub;
- object-lock/WORM retention.

Those limitations remain explicit in the aggregate and receipt. The workflow
has no repository write, package write, deployment, trading, broker or
credential authority.

## Failure semantics

Generation fails closed when:

- a runner label is not one of the two approved labels;
- both claims came from the same runner;
- any recipe or toolchain binding differs;
- the retained complete-input content root differs from the current deployable
  console source tree;
- archive bytes or any parsed image identity differs;
- the OCI archive is malformed, truncated, contains duplicate names, has
  mismatched blob digests, or lacks rewritten timestamps;
- the aggregate is not signed and identity-verified by the OIDC job.

No fallback converts a mismatch into a weaker digest-only pass.
