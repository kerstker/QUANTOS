# Governed Evidence Byte Portability Design

Date: 2026-07-18
Status: independently reviewed; implementation in progress
Safety level: `L2_DRY_RUN_ONLY`

## 1. Problem statement

QUANTOS deliberately verifies governed artifacts by hashing their exact bytes.
That is the correct security property only when Git materializes the same bytes
on every supported checkout and every generator writes the same bytes on every
supported operating system. The pre-fix audit found that the repository
satisfied neither side of that contract consistently.

The retained container evidence binds these three recipe inputs:

- `deploy/console.Dockerfile`
- `requirements.txt`
- `.dockerignore`

The retained sizes sit between the canonical LF Git blobs and the current
Windows CRLF worktree files. The files and evidence entered the repository in
the same historical change, so the evidence was produced from a mixed-EOL
working tree that a clean checkout cannot reproduce. This causes four
canonical-gate groups to fail from one root cause: container audit, container
reproducibility, the derived supply-chain baseline, and the all-suites pytest
group that verifies the baseline.

The portable trial-ledger bundle has the same latent defect for its history.
Its seal hashes the exact bytes of
`evidence/alpha_trial_ledger_history.json`, but `.gitattributes` only marked the
SQLite snapshot as binary. A fresh checkout with `core.autocrlf=true` could
therefore rewrite the history and make an untampered bundle fail verification.
The seal JSON is also LF-pinned for repository consistency, but it is validated
by strict fields/semantics rather than by a self-digest; its whitespace alone is
not part of the cryptographic claim.

The pre-fix supply-chain baseline raw-hashes 150 text paths. The target manifest
adds `.gitattributes` and its portability governance test, producing 152 paths
so the control self-binds. All original 150 Git blobs are LF, but none had an
EOL attribute: the pre-fix Windows worktree materialized 144 as
CRLF, four as mixed EOL, and only two generated files remain LF by accident.
Five evidence generators also use `Path.write_text` without an explicit newline
contract, so a Windows refresh can write CRLF bytes that Git later normalizes.
Pinning only the three container recipe files would therefore leave the larger
manifest non-portable.

A separate reporting defect exists in the live ledger preflight:
`inspect_ledger` accepts live schema versions greater than or equal to 2, but
the capture-ready branch reports the constant `2` instead of the actual
`PRAGMA user_version`.

## 2. Decision

Keep exact-byte hashing and make checkout bytes deterministic. Do not normalize
line endings inside a verifier.

Add this minimal, type-scoped attribute contract:

```gitattributes
.gitattributes text eol=lf
*.py text eol=lf
*.json text eol=lf
*.toml text eol=lf
*.txt text eol=lf
*.yml text eol=lf
*.ps1 text eol=lf
deploy/console.Dockerfile text eol=lf
.dockerignore text eol=lf
evidence/alpha_trial_ledger_snapshot.db binary
```

The final line already exists and remains the binary rule. These rules cover all
152 target manifest paths, the three direct recipe inputs, the portable-ledger
JSON, and the generated supply-chain JSON without imposing a repository-wide
`* text=auto` rule. They are repository contracts, not editor preferences.
`.editorconfig` continues to request LF for editors, while `.gitattributes`
controls Git checkout behavior.

Every raw-hashed JSON writer must emit canonical UTF-8/LF bytes directly.
Writers that currently call `Path.write_text` are changed to encode the existing
canonical JSON string and call `write_bytes`; writers that already call
`write_bytes` remain unchanged. Git attributes are not a substitute for this
writer contract because verification can run immediately after generation,
before Git filters any file.

After the attributes are added, the three recipe inputs are normalized to their
canonical LF Git bytes. The old container evidence remains invalid because it
proves builds made from different raw input bytes. It must not be re-hashed,
patched, or declared equivalent. A new pair of cold OCI builds and a new Trivy
scan are required before the corresponding evidence can become current.

The same honesty rule applies to retained audit bindings. npm and Python audit
evidence bind exact package/requirements manifest bytes, so both standalone
checks become invalid after the LF migration and require real advisory audits
against the LF manifests; their old reports must not be patched with new hashes.
The Actions OSV standalone evidence remains semantically current, but the old
supply-chain baseline raw-hashes its pre-migration artifact bytes. The derived
baseline/SBOM can be regenerated only after npm, Python, container audit, and
container reproducibility inputs are honestly current.

## 3. Rejected alternatives

### 3.1 Normalize CRLF to LF before hashing

Rejected. The container build consumes worktree bytes, not a verifier's
normalized copy. Normalizing only during verification would claim that an OCI
image was built from bytes that were never supplied to BuildKit.

### 3.2 Accept either LF or CRLF hashes

Rejected. A hash allowlist with multiple platform variants weakens one recipe
identity into several identities and still cannot account honestly for the
mixed-EOL bytes retained today.

### 3.3 Skip exact-byte checks on Windows

Rejected. A platform-specific bypass would make the strongest local
verification environment the least trustworthy and would allow real recipe
drift to hide behind an operating-system exception.

## 4. Ledger schema reporting contract

`inspect_ledger` returns both:

- `ledger_contract_version`: the actual `PRAGMA user_version` read twice from
  the live database;
- `required_ledger_contract_version`: the minimum supported version, currently
  `2`.

The live `--check` policy remains forward-compatible: version 2 or later is
accepted only when the required tables, columns, triggers, and exact
search-to-evaluation bindings pass semantic verification. The portable snapshot
policy is tightened to exact version 2 because its table and trigger
surface is a frozen published contract.

This live `>=2` check verifies only the trial-ledger v2 subset inside a newer
ExperimentStore. It is not a health check for every future table added by
schema v3. The reviewed White/SPA Phase-A design requires a separate
exact-version semantic family-authority verifier, but that verifier is not
implemented by this portability change. Until it lands, a reported live v3
fixture proves only that the v2 subset is forward-readable; it authorizes no
family assessment.

Operator documentation must say `schema v2-or-later` for the live authority and
`schema v2 exactly` for the portable snapshot.

## 5. Files and boundaries

- `.gitattributes` owns checkout byte semantics for all current governed text
  types and the explicit binary SQLite snapshot.
- `scripts/generate_supply_chain_evidence.py::_MANIFESTS` includes both
  `.gitattributes` and the portability governance test, so removing or weakening
  either control invalidates the next baseline check.
- `scripts/generate_{actions,npm,python,container}_audit_evidence.py` and
  `scripts/generate_container_reproducibility_evidence.py` own explicit LF
  emission for their canonical JSON artifacts.
- `scripts/generate_{event_schema_catalog,service_import_graph,supply_chain_evidence}.py`
  already write canonical bytes and remain regression fixtures.
- `tools/search_trial_ledger_history.py` reports actual and required live schema
  versions and continues to hash exact portable bytes.
- `tests/tools/test_search_trial_ledger_history.py` covers live-version
  reporting and exact portable-version rejection.
- `tests/governance/test_evidence_checkout_portability.py` verifies every
  `_MANIFESTS` path plus the direct recipe/ledger paths through `git check-attr`,
  raw-byte inspection, an isolated `core.autocrlf=true` checkout, and writer
  byte fixtures.
- `docs/runbooks/l2-research-operations.md` documents the live/portable version
  distinction.
- The portable-ledger design document is corrected to state that `.gitignore`
  changed to remove the obsolete workflow marker and that the JSON artifacts
  are LF-pinned.
- The paper-gap report names `a38c8d5` as the portable-ledger closure baseline
  and describes the EOL/raw-byte root cause without predicting a new full-gate
  count.

No verifier imports Git at runtime. Git is used only by a repository governance
test to verify repository attributes. Runtime and CI artifact verification
continues to read and hash files directly.

## 6. Test strategy

The implementation follows a red-green cycle.

1. A governance test asks `git check-attr text eol` for all 152 manifest paths,
   `.dockerignore`, and the two portable-ledger JSON files; each must report
   `text: set` and `eol: lf`. It also requires the SQLite snapshot to be
   `binary: set` and `text: unset`.
2. The same governance test reads every governed text worktree file as raw bytes
   and rejects CRLF or lone CR. It then materializes the paths into an isolated
   temporary checkout with `core.autocrlf=true` and proves the resulting bytes
   are still the canonical LF bytes. Attribute metadata alone is not accepted.
3. A parameterized writer test calls all eight governed evidence writers with
   non-ASCII content and requires the exact existing canonical JSON bytes for
   each writer's declared Unicode policy, exactly one final LF, and no CR. On
   Windows the five default-`write_text` implementations fail before the fix.
4. A tool test creates a live schema-v3 registry and proves that
   `inspect_ledger` reports actual version 3 and required version 2.
5. A new live `--check` v3 regression remains green and is explicitly scoped to
   the v2 trial-ledger subset. The future White/SPA Phase-A plan must add the
   separate corrupt-family-table fixture together with its exact verifier; it
   is not an acceptance claim of this change.
6. A new portable `--check-snapshot` unknown-version test continues to
   reject version 3 even after the seal is re-hashed.
7. The committed portable bundle passes exact-byte verification after the LF
   attributes are in place.
8. npm/Python audit, container audit/reproducibility, supply-chain, and the
   downstream all-suites supply-chain tests continue to fail honestly until new
   manifest/recipe-matching external evidence is supplied. The Actions OSV
   standalone check stays green but its raw artifact is rebound only when the
   derived baseline is legitimately regenerated. Tests must not rewrite any
   artifact merely to make the gate green.

## 7. Migration and operational sequence

1. Add and test the Git attributes.
2. Normalize every current governed text path to its LF Git-blob bytes, but only
   after proving newline normalization is the sole worktree difference.
3. Make the five platform-dependent evidence writers emit bytes explicitly and
   verify all eight writers.
4. Verify the portable trial-ledger bundle and live ledger separately.
5. Correct schema reporting and documentation.
6. Leave npm/Python audit, container, and supply-chain evidence red; verify the
   Actions OSV evidence independently remains current.
7. In explicitly authorized advisory-audit and Docker/BuildKit/Trivy
   environments, rerun npm/Python audits against the LF manifests, perform two
   cold builds from the LF recipe, scan the pinned base and resulting runtime
   image, then regenerate the derived supply-chain baseline/SBOM and rerun the
   canonical gate.

The external build and scan are not part of the local code fix. The code fix is
complete when the repository has one cross-platform recipe identity and the
gate fails only for genuinely absent or stale evidence rather than checkout
translation.

## 8. Safety and authorization

- No broker, order, credential, live-trading, or network path is added.
- All five L2 safety switches remain unchanged.
- No external backup is refreshed by this work.
- No Docker image, Trivy database, OCI archive, Git stage, or Git commit is
  created without separate authorization.

## 9. Acceptance criteria

The local portability change is accepted only when:

- all current supply-chain manifest text paths plus the direct recipe/ledger
  paths are Git-pinned to LF and the SQLite snapshot remains binary;
- every governed JSON writer emits canonical UTF-8/LF bytes independently of
  the host OS;
- raw-byte checks and an isolated `core.autocrlf=true` materialization both
  reproduce the canonical LF bytes;
- a live schema-v3 registry is truthfully reported as actual version 3 while
  the required minimum remains 2;
- live `>=2` and portable `==2` policies are independently tested;
- the committed portable ledger verifies without access to `data/cache`;
- no verifier normalizes content or accepts multiple hashes;
- documentation no longer claims `.gitignore` was unchanged or cites the
  pre-ledger commit as the closure baseline;
- npm/Python audit evidence remains fail-closed until real LF-manifest audits,
  container evidence remains fail-closed until a real LF-recipe rebuild/scan,
  and the derived baseline remains stale until all upstream evidence is current;
  and
- the governance scanner reports no forbidden live, broker, credential, or
  network implementation.
