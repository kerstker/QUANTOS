# Governed Model-Risk Baseline Design

## Goal

Create a durable, tamper-evident baseline for model cards, tool lineage, offline
evaluations, agent-boundary red-team results, and challenger decisions. The
baseline must use existing real QUANTOS artifacts and must not imply model edge,
production deployment approval, or live-trading authority.

## 2026-07-28 Authority Hardening

The local SQLite registry is now a schema-v2 authority rather than a
best-effort artifact cache. Artifact records and runtime invocations each form
an append-only, contiguous SHA-256 chain. Transactional metadata pins both row
count and chain head, so mutation, deletion, tail truncation, sequence gaps, and
replay conflicts fail closed on open and verification.

The store uses strict tables, WAL, `synchronous=FULL`, exact schema and trigger
contracts, and update/delete denial triggers. No-op trigger replacement and
schema injection are rejected. Generated evidence publishes the exact store
kind/schema plus both chain heads, and the top-tier assessor independently
recomputes the artifact head.

The current refresh retains 61 immutable artifacts, 10 runtime events, and 6
history revisions. This hardening does not create a real external provider,
externally authenticated challenger, or positive model edge; maturity therefore
remains M2/PARTIAL.

## Real Evidence Sources

1. `data/cache/ml_stack_snapshot_fx.json`: real FX purged/embargoed walk-forward OOS；crypto 研究写入独立的 `ml_stack_snapshot_crypto.json`
   comparison of Ridge, GBM, and the selected single-factor baseline. All three
   Sharpe values are negative and the source verdict is `REFUTED_NO_EDGE`.
2. `services/intelligence/guardrails.py` and `ResearchAgentLoop`: executable
   non-human boundaries for approval, kill-switch reset, live trading, and
   credential access.
3. Implementation and source-data SHA-256 commitments supplied by the evidence
   generator.

## Contracts

Frozen contracts cover:

- model cards with owner, intended use, prohibited use, implementation digest,
  dataset reference, prompt lineage status, and tool manifest reference;
- tool manifests with allowed and prohibited action sets;
- immutable offline evaluation artifacts;
- red-team case results and aggregate reports;
- challenger reviews with distinct owner/reviewer groups and an absolute metric
  floor.
- external-provider bindings that contain deployment and verification-policy
  references but cannot contain credentials;
- external-provider response envelopes that bind provider identity, request
  digest, response digest, receipt reference, and caller-supplied time.

All wall-clock values are caller supplied. Non-finite metrics, duplicate case
IDs, self-comparison, and owner/reviewer group reuse fail closed.

## Registry

`services/governance/model_risk_registry.py` owns an append-only artifact table
and a hash-chained runtime invocation ledger. Each artifact stores canonical
JSON plus a stable content hash. Re-recording the same artifact is idempotent;
changing content under an existing ID raises. Reading an uninitialized or
corrupt store fails closed. Verification recomputes every stored hash and every
runtime-chain link.

`services/intelligence/runtime.py` now has two research-only paths. The original
path runs caller-supplied offline compute. The external path accepts only an
injected provider and verifier, requires a registered provider binding, hashes a
canonical request, validates provider/model identity and response integrity,
requires an external verification receipt, and then applies the same action and
sensitive-output policy. Missing verification defaults to denial. Denied,
tampered, and failed calls never enter the allowed-invocation ledger. The module
imports no network or model-provider SDK and owns no credentials.

## Honest Maturity Boundary

The local implementation establishes M2 only. It cannot reach M3 because:

- no real external model provider deployment or externally verifiable provider
  receipt has been configured; test fixtures prove only fail-closed behavior;
- challenger review separation is a process role, not externally authenticated
  identity;
- the real ML result is a null result, not a deployable model.

The deterministic PIT evaluation subset and longitudinal registry history are
now retained. These remaining limits are evidence, not failures to conceal.

## M3 Exit Conditions

1. Configure a real external provider outside the runtime process and register
   its credential-free deployment binding.
2. Verify real provider receipts through an external trust policy and retain
   successful invocation evidence.
3. Bind challenger review to strong external identity and separation of duties.
4. Keep refuted models excluded until a genuine positive OOS challenger exists.
5. Continue immutable evaluations and red-team revisions across model, prompt,
   provider-binding, and policy changes.

