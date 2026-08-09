# L2 internal strong-identity deployment design

## Decision

QUANTOS will use a private, internal-only OpenID Connect deployment rather
than inventing a local password or token protocol.  The selected topology is:

- Keycloak as the internal OIDC issuer;
- a private X.509 authority for operator client certificates;
- direct mTLS at the Keycloak token endpoint so Keycloak can issue RFC 8705
  certificate-bound access tokens;
- Envoy as the mTLS ingress for `services/promotion_api`;
- a Unix-domain-socket-only hop from Envoy to the promotion API;
- the existing `OidcMtlsVerifier`, OIDC key registry, identity-authorization
  registry, access-review attestation, RBAC, approval state machine, and audit
  stores remain the application authority.

This is an L2 governance deployment.  It does not create execution,
deployment, credential, order-submission, live-trading, capital-allocation, or
kill-switch-reset authority.

## Why this shape

Keycloak documents Authorization Code + PKCE, WebAuthn/step-up authentication,
and OAuth 2.0 mutual-TLS certificate-bound access tokens.  It can therefore
produce the `iss`, `sub`, `aud`, `iat`, `nbf`, `exp`, `auth_time`, `jti`, `acr`,
`amr`, `roles`, and `cnf.x5t#S256` claims already required by QUANTOS.  Keycloak
must receive the client certificate at its own TLS endpoint; a bearer-only
reverse-proxy deployment is not acceptable.

Envoy documents downstream client-certificate verification and
`SANITIZE_SET` handling for `X-Forwarded-Client-Cert`.  The application hop is
not a general network listener: it is a permission-restricted Unix domain
socket shared only by Envoy and the promotion API.  A separate adapter will
translate the sanitized certificate envelope into QUANTOS' trusted ASGI TLS
scope.  The adapter is not permitted to trust an arbitrary HTTP header or a
TCP peer merely because it is on an internal subnet.

Primary references:

- <https://www.keycloak.org/server/mutual-tls>
- <https://www.keycloak.org/docs/latest/server_admin/>
- <https://www.keycloak.org/securing-apps/specifications>
- <https://www.envoyproxy.io/docs/envoy/latest/start/quick-start/securing.html>
- <https://www.envoyproxy.io/docs/envoy/latest/api-v3/extensions/filters/network/http_connection_manager/v3/http_connection_manager.proto.html>
- <https://asgi.readthedocs.io/en/latest/specs/tls.html>

## Trust domains

The deployment has four deliberately separate authorities:

1. **OIDC authentication authority**: Keycloak authenticates the human and
   signs a short-lived access token.  Realm administration is not available to
   QUANTOS processes.
2. **X.509 possession authority**: the private CA issues a short-lived client
   certificate to an operator device.  The browser presents the same
   certificate to Keycloak and the governance ingress.
3. **Application authorization authority**: the append-only QUANTOS identity
   registry grants an exact `issuer|subject` a reviewed set of governance
   roles.  IdP token roles alone never authorize an action.
4. **Business approval authority**: the existing multi-party promotion
   workflow requires distinct verified actors and consumes a one-way grant.

Compromise of any one authority must therefore be insufficient.  A stolen
token fails without its client certificate; a valid certificate fails without
a signed token; both fail without a current application grant; a single
authorized person still cannot complete the multi-party workflow alone.

## Required deployment contract

Every concrete deployment supplies a secret-free, digest-pinned manifest.  A
manifest is admissible only when all of the following are true:

- readiness is exactly `L2_DRY_RUN_ONLY`;
- the issuer and all browser endpoints are canonical HTTPS DNS URLs;
- the issuer is classified `INTERNAL_ONLY` and has no public exposure;
- the OIDC image is pinned by immutable SHA-256 digest;
- Authorization Code is enabled and PKCE is exactly `S256`;
- implicit, password/direct grant, device, CIBA, token exchange, service
  account, and offline-access grants are disabled for the browser client;
- the browser client is public and has no client secret;
- RFC 8705 certificate-bound access tokens are enabled;
- token lifetime is no longer than the existing verifier policy;
- LoA 2 is requested and returned as `urn:quantos:aal2`;
- successful password and WebAuthn executions produce an `amr` value that
  includes the configured required reference;
- governance roles are emitted in the top-level `roles` claim and are limited
  to the existing `GovernanceRole` enum;
- mTLS client authentication is required at both Keycloak and Envoy;
- Envoy validates the private CA, rejects expired certificates, sanitizes and
  replaces certificate forwarding data, and cannot reach the application over
  a general TCP listener;
- the promotion API accepts only the restricted Unix socket;
- OIDC verification keys are imported into the existing append-only registry,
  pinned by digest and minimum generation, and selected by `kid`;
- identity authorization stays empty by default and requires independent
  access-review attestations before any principal is enabled;
- every execution/deployment/live/credential/order authority flag is false.

The manifest contains no password, private key, bearer token, client secret,
recovery code, broker credential, or certificate private key.  Such fields are
rejected rather than ignored.

## Authentication flow

1. The operator receives a device-bound client certificate through an
   out-of-repository ceremony.  The root CA is installed only on approved L2
   operator devices and internal services.
2. The browser opens the console over HTTPS and starts the existing
   Authorization Code + PKCE S256 flow.
3. Keycloak requests and validates the client certificate, then requires the
   configured LoA 2 browser flow.  The initial profile requires password plus
   WebAuthn; TOTP may be an explicitly reviewed fallback, never a silent
   downgrade.
4. Keycloak returns a short-lived access token whose `cnf.x5t#S256` is bound to
   the certificate used at the token endpoint.
5. The browser calls the governance ingress with that token and the same
   certificate.  Envoy validates mTLS and replaces any incoming XFCC data with
   its own certificate envelope.
6. The trusted socket adapter constructs the TLS certificate scope.  The
   existing verifier checks signature, issuer, audience, time, LoA, AMR, role,
   `kid`, key status, and certificate thumbprint.
7. The existing application registry evaluates the exact `issuer|subject`,
   token epoch, revocation, HUMAN/WORKLOAD class, role intersection, and access
   review.  The decision is durably recorded before the request is accepted.

Any missing, malformed, expired, stale, unreviewed, unbound, revoked, or
unavailable input fails closed.  Timeout, 429, an empty result, or an
unavailable authority is never approval.

## Bootstrap and recovery

- No realm user, password, bootstrap administrator secret, private key, or
  recovery code is committed.
- The first Keycloak administrator is created from an operator-controlled
  secret source and removed from the normal login path after bootstrap.
- At least three distinct HUMAN subjects are enrolled for requester, reviewer,
  and approver duties.  Executor assignment is separately reviewed.
- Keycloak role mappings and QUANTOS application grants are two different
  changes and require different audit records.
- A new OIDC signing key is added to Keycloak first, then imported as a new
  `kid` in the QUANTOS registry, then activated for token signing.  Old keys are
  retained and may only transition ACTIVE to REVOKED.
- User disablement, application-registry revocation, and certificate
  revocation/expiry are independent emergency controls.  Loss of the review
  system must never prevent access removal.
- Authority databases, realm exports without users/secrets, public-key
  registries, CRLs when available, and access-review evidence are included in
  the existing encrypted L2 backup source contract only after a separate
  source-contract review.

## Certificate-lifecycle boundary

The first internal deployment may use short-lived certificates and passive
revocation for L2 operation.  That limits but does not eliminate the interval
in which a compromised, already-issued certificate remains usable.  It is
therefore not M4 evidence.  Closing the institutional strong-identity red line
requires an independently operated certificate lifecycle with retained
issuance, active revocation status (CRL or OCSP), renewal, compromise, and
restore evidence.  The application must not relabel short lifetime alone as
instant revocation.

## Delivery slices

### Slice 1 - machine deployment contract

- deterministic manifest models and loader;
- fail-closed compatibility check against `OidcTrustPolicy`;
- explicit `DESIGN_READY_DEPLOYMENT_PENDING` status when no retained runtime
  proof exists;
- adversarial tests for public exposure, grant downgrade, missing mTLS,
  header trust, mutable image tags, role widening, and authority escalation.

### Slice 2 - trusted transport adapter and provider templates

- Keycloak realm/client template with no users or secrets;
- Envoy downstream-mTLS and UDS-only upstream configuration;
- adapter that accepts only Envoy-sanitized certificate data on the restricted
  socket and emits the trusted TLS scope;
- end-to-end negative tests for direct socket access and spoofed XFCC.

### Slice 3 - operator provisioning

- initialize the private CA and Keycloak outside the repository;
- enroll real distinct operators and WebAuthn credentials;
- import and pin OIDC public keys;
- provision reviewed application grants;
- retain token/certificate metadata and decisions without retaining secrets.

### Slice 4 - operational evidence

- key rotation and one-way revocation drill;
- joiner/mover/leaver and independent access-review drill;
- certificate expiry/revocation and backup/restore drill;
- browser PKCE + mTLS + MFA + RBAC end-to-end evidence;
- only then reassess `GOV.IDENTITY.STRONG_AUTH` maturity.

## Acceptance and honest status

Slice 1 may be accepted entirely offline.  Slices 2-4 require an actual internal
deployment and operator ceremony.  Until those exist:

- governance commands remain disabled in production;
- the identity registry remains empty by default;
- `GOV.IDENTITY.STRONG_AUTH` remains M3/PARTIAL;
- Testnet soak eligibility is unchanged;
- Mainnet, Micro-Live, and real-capital operations remain structurally blocked.
