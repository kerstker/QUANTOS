# CI reproducibility closure

## Scope

Close the locally achievable remainder of institutional roadmap Rank #28 without
changing any trading or runtime authority. The change is limited to build/test
inputs and CI verification.

## Contracts

1. CI installs Python dependencies from a generated, hash-locked CPython 3.12
   Ubuntu 24.04 x86-64 artifact set (allowing the runner's compatible manylinux
   2.28/2.17 wheel tags). The lock records the exact source requirement digests,
   target interpreter and target platform. Missing hashes, non-HTTPS artifacts,
   duplicate packages, source drift or a different interpreter fail closed.
2. The backend coverage job reuses the two canonical pytest command manifests
   from `scripts/validate.py`; it must not maintain a second test-directory list.
   It measures branch coverage for `quantos/`, `services/`, `live/` and `tools/`
   and enforces a versioned floor.
3. The frontend runs Vitest V8 coverage over the complete `src/` tree with
   explicit line, statement, function and branch floors.
4. Coverage floors are regression floors, not a claim that every reachable path
   is tested. Raising them is compatible; weakening them requires an explicit
   policy change and review.
5. The canonical validation gate remains the functional release authority. CI
   coverage and dependency-lock jobs are additional fail-closed required checks,
   not substitutes for `scripts/validate.py`.

## Safety boundary

The implementation performs no exchange access, credential loading, mandate
creation, order submission, risk-limit selection or live-state mutation. All five
L2 safety flags remain unchanged.
