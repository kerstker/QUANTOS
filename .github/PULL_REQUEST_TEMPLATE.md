## Purpose

<!-- What problem does this solve, and why is this change needed now? -->

## Scope

<!-- Main modules/files. State explicitly what is out of scope. -->

## Evidence

<!-- Failing test first, focused regression, full gate, screenshots, or machine artifacts. -->

- [ ] New behavior was test-first, or this is documentation/generated evidence only.
- [ ] `python scripts/check_repository_hygiene.py` passes when layout/public docs changed.
- [ ] `python scripts/validate.py` passes, or the exact blocker is documented.
- [ ] Frontend changes pass typecheck, ESLint, tests, and build.
- [ ] API contract changes update backend, TypeScript consumers, and contract tests together.

## Safety review

- [ ] L2 flags remain safe; Mainnet and AI-directed trading are not enabled.
- [ ] No secret, credential, passphrase, signed mandate, raw runtime data, or authority database is included.
- [ ] No approval, attestation, risk, kill-switch, reconciliation, or audit boundary is weakened.
- [ ] Missing/stale/malformed/timeout/429/empty evidence still fails closed.
- [ ] Network access remains isolated to approved read-only tools or the guarded live adapter boundary.
- [ ] Point-in-time and no-lookahead behavior is preserved.

## Operational impact

<!-- Migration, rollback, schedule, deployment, data freshness, or monitoring impact. Use “none” explicitly. -->

## Remaining risks and blockers

<!-- A green test is not the same as candidate admission or trading authority. -->
