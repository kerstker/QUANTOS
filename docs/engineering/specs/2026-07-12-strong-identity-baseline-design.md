# Strong Identity Baseline Design

## Goal

Replace the promotion API's local shared token and self-asserted operator label
with a load-bearing strong-identity boundary. The boundary verifies an
asymmetrically signed OIDC access token, binds it to a client certificate, and
enforces role-specific promotion actions. It must fail closed when external
identity or trusted TLS context is absent.

This increment remains `L2_DRY_RUN_ONLY`. Strong identity for alpha promotion
does not authorize live trading, broker access, credentials, or order submission.

## Baseline Defect

Before this increment, `services/governance/promotion_api/main.py` created a local
bearer secret and accepted `X-Operator` as the actor. Any process holding that
secret could submit three different labels and complete the nominal three-person
workflow. The replacement below removes that path.

## Verification Boundary

The new verifier requires all of the following:

1. A JWT access token signed by an allowlisted asymmetric algorithm (`EdDSA` or
   `RS256`); symmetric algorithms and `none` are never accepted.
2. Exact OIDC issuer and audience checks.
3. Caller-supplied time checks for `iat`, `nbf`, `exp`, `auth_time`, maximum token
   lifetime, and maximum authentication age.
4. Required `acr` and `amr` values, including MFA.
5. RFC 8705 certificate binding through `cnf.x5t#S256`.
6. A verified client certificate supplied through a trusted ASGI TLS extension;
   certificate headers from the request are never trusted.
7. Explicit governance roles for read, request, review, approve, and execute.

The verifier returns a frozen identity whose actor ID is derived from the OIDC
issuer and immutable subject. Request bodies and headers cannot choose actor IDs.

## Promotion API Integration

- Delete local governance-token creation and comparison.
- Delete all `X-Operator` handling.
- Derive every approval actor and audit actor from the verified identity.
- Require action-specific roles at each endpoint.
- Preserve the existing three-distinct-subject state machine and one-way grant
  consumption.
- Keep `/health` open and static, but advertise that strong identity is required
  and legacy shared-token authentication is rejected.
- Default to unavailable when OIDC public-key policy is not configured.

## Frontend Boundary

The console holds a short-lived OIDC access token only in module memory. A
versioned PKCE transaction containing state, nonce, verifier, creation time, and
configuration bindings may use session storage; it is removed before the token
exchange. Authorization Code + PKCE S256 uses Web Crypto and no client secret.
The console sends `Authorization: Bearer`; it never sends an operator label or
local shared secret. Browser/client certificate selection remains a TLS concern
outside JavaScript. Operational integration with an external IdP remains an
explicit production follow-on.

## Honest Maturity

This increment can establish M2 only. M4 still requires external evidence for:

- an operating OIDC issuer, end-to-end browser login evidence, and
  lifecycle-managed signing keys;
- a deployed TLS terminator that validates client certificate chains;
- identity lifecycle, revocation, rotation, access review, and SLO evidence;
- independent operational proof that AI workloads cannot obtain human identity.

No generated test key, self-signed certificate, local header, or process role may
be counted as external identity evidence.
