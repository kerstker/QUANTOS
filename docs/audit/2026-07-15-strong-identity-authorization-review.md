# Strong Identity Application Authorization Review - 2026-07-15

## Decision

`GOV.IDENTITY.STRONG_AUTH` advances from M2 to M3 but remains PARTIAL and a
top-tier red line. OIDC, mTLS, and MFA establish authenticated identity; they no
longer make token `roles` the final application authorization fact.

## Landed Local Controls

- An empty-by-default generationed registry owns enrollment for exact
  `issuer|subject` principals.
- Each grant records HUMAN or WORKLOAD class, enabled/revoked state, allowed
  governance roles, `tokens_valid_after_ms`, and access-review expiry.
- Authorization is the intersection of the verified token role and the
  application grant. A token cannot enroll itself or raise its own privilege.
- WORKLOAD principals cannot request, review, approve, or execute human
  governance actions. Revoked, disabled, stale-review, old, future, and expired
  identities fail closed.
- Exact registry snapshots and authorization decisions share one append-only,
  hash-chained SQLite authority with generation CAS, WAL/FULL durability,
  consistent read snapshots, and future/rollback clock guards.
- The promotion API opens the authority with `create=False`, verifies the
  repository registry mirror, records the decision before returning, and
  discards a stale ALLOW if the registry changes before commit.
- All decisions keep execution, deployment, credential, order-submission, and
  live-trading authority fixed to false.

## Verification

Focused identity, store, promotion-boundary, and endpoint tests: `68 passed`.
The suite includes token self-enrollment, role self-escalation, revocation,
review expiry, token epoch, WORKLOAD human-action denial, authority absence,
and registry revocation after evaluation but before decision record.

## Current Production Reality

- `config/identity_authorization_registry.json` is generation zero and empty.
- The fixed production identity-authorization database is not provisioned.
- The promotion API therefore fails closed until an explicit operator-managed
  authority is provisioned; no principal is implicitly authorized.
- L2 dry-run restrictions remain unchanged.

## Open Institutional Gaps

1. No externally operated IdP, trusted mTLS terminator, or deployment evidence.
2. No managed signing-key custody, rotation, compromise, or revocation proof.
3. No HR/IAM joiner-mover-leaver feed or independent periodic access-review
   evidence. The local fields and enforcement exist, but external operations do
   not.
4. HUMAN/WORKLOAD classification is locally enforced but not independently
   attested by an external workload identity system.
5. The SQLite authority is local, replaceable, unsealed, and not independently
   deployed or replicated.

These external gaps prevent M4 and any live-trading authorization claim.
