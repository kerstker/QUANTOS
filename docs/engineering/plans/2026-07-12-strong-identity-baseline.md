# Strong Identity Baseline Plan

## M2 Baseline

- [x] Add frozen identity and governance-role contracts.
- [x] Add asymmetric OIDC verification with deterministic time checks.
- [x] Require RFC 8705 mTLS certificate binding and trusted ASGI TLS context.
- [x] Add role enforcement and adversarial identity tests.
- [x] Remove shared governance token and self-asserted operator handling.
- [x] Thread verified identities through every promotion endpoint and audit event.
- [x] Update the console client to Bearer authentication without operator labels.
- [x] Implement Authorization Code + PKCE S256 with Web Crypto, one-time
      versioned transactions, and memory-only access tokens.
- [x] Generate fail-closed strong-identity evidence with explicit M4 blockers.
- [x] Replace the manual capability checker with an M2 evidence checker.
- [x] Regenerate supply-chain and top-tier assessment artifacts.
- [x] Run the canonical full validation gate (`2003 passed, 1 skipped`; live stack
      `844 passed`; frontend `68 passed`; governance scan `244 files / 0 violations`).

## M4 Follow-On

- [ ] Deploy against an external OIDC issuer with managed key rotation.
- [ ] Deploy a trusted mTLS terminator and retain certificate-policy evidence.
- [ ] Retain end-to-end browser login evidence from the deployed external issuer.
- [ ] Prove joiner/mover/leaver, revocation, access-review, and incident workflows.
- [ ] Prove AI workloads cannot acquire human identity or approval roles.

Strong identity for research promotion never enables live trading.
