# OIDC verification-key rotation and revocation registry

## Decision

The governance identity verifier no longer accepts one static public-key file.
It requires an operator-provided `OidcKeyRegistry` and selects the verification
key by the JWT header `kid` before decoding claims.

Each registry key binds:

- a canonical unique `kid`;
- exactly one allowed asymmetric algorithm (`EdDSA` or `RS256`);
- public-key material only;
- ACTIVE or REVOKED status;
- an explicit validity interval.

EdDSA entries must contain an Ed25519 public key. RS256 entries must contain an
RSA public key of at least 2048 bits. Private keys are rejected.

## Rotation and rollback rules

Registry generations are contiguous. An exact snapshot replay is idempotent,
but a different document at the same generation, rollback, or generation gap is
rejected. During rotation:

- an existing `kid` cannot disappear;
- its algorithm, public key, or validity interval cannot change;
- ACTIVE may become REVOKED;
- REVOKED can never become ACTIVE again;
- a new key must use a new `kid`.

Runtime configuration requires all of:

- `QUANTOS_OIDC_KEY_REGISTRY_FILE`;
- `QUANTOS_OIDC_KEY_REGISTRY_SHA256`;
- `QUANTOS_OIDC_KEY_REGISTRY_MIN_GENERATION`.

The loader rejects symlinks, digest mismatch, issuer mismatch, and any registry
below the operator-pinned generation. The legacy single-key
`QUANTOS_OIDC_PUBLIC_KEY_FILE` configuration is not accepted.

## Verification order

After the asymmetric algorithm allowlist and `at+jwt` type check, key selection
fails closed on missing/unknown `kid`, revoked key, algorithm mismatch, or key
validity failure. Only then is the JWT signature and full issuer/audience/time/
MFA/RFC 8705 certificate-binding contract evaluated.

## Honest boundary

This makes rotation and revocation load-bearing at the application boundary. It
does not operate an external IdP, fetch JWKS over the network, prove managed HSM
custody, or provide an independently operated lifecycle authority. The registry
snapshot and pins must still be supplied by an operator-owned deployment.
`GOV.IDENTITY.STRONG_AUTH` therefore remains M3/PARTIAL and the default L2
system remains fail closed with no external identity configuration.

The module contains no signing key, network client, credential access, broker,
order-submission, deployment, or live-trading capability.
