# Strong-identity access-review attestation

## Decision

An enabled governance principal can no longer rely on a caller-supplied access
review expiry alone. Every enabled, non-revoked `IdentityAuthorizationGrant`
must embed an `IdentityAccessReviewAttestation` created from an already verified
OIDC + mTLS + MFA identity.

The attestation binds:

- the exact immutable `issuer|subject` principal;
- HUMAN or WORKLOAD classification;
- the complete ordered role grant;
- an independent reviewer `issuer|subject`;
- the reviewer's assurance, token ID, and client-certificate digest;
- review time and a maximum 90-day expiry;
- a canonical content hash and derived review ID.

The reviewer must hold `PROMOTION_REVIEWER`, its verified token must be valid at
the review instant, and self-review is structurally rejected. The grant validates
the subject, class, roles, and expiry against the attestation. Authorization
decisions retain the review ID, content hash, and reviewer actor ID. Because the
complete grant is persisted in the existing append-only registry snapshot, the
review is covered by the same registry digest, CAS head, and authority hash
chain used by runtime authorization.

## Failure semantics

The following fail closed:

- enabled access with no attestation;
- self-review or a reviewer without the reviewer role;
- reviewer tokens issued after, not yet valid at, or expired at review time;
- review validity longer than 90 days;
- content-hash, subject, principal-class, role, or expiry drift;
- authorization after the reviewed expiry.

Emergency revocation remains asymmetric: a disabled and revoked grant may omit
a fresh review. Loss of the review system therefore cannot prevent access from
being removed.

## Honest boundary

This closes the local load-bearing evidence gap between a review timestamp and
an identifiable independent strong reviewer. It does not prove that an external
IdP, trusted mTLS terminator, managed certificate/key lifecycle, joiner/mover/
leaver process, or independent organization is operating. The default registry
remains empty generation zero and fail closed. `GOV.IDENTITY.STRONG_AUTH`
therefore remains M3/PARTIAL until real external operational evidence exists.

The module is deterministic and offline. It has no credential, deployment,
broker, order-submission, or live-trading capability.
