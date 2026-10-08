# Binance resident full-audit renewal

Status: implemented and locally verified; immutable DGX release acceptance is
required before the production incident is closed.

## Production evidence

On 2026-08-26 the DGX exposed two coupled scaling failures in the public
Binance realtime authority:

- the cursor journal had approximately 45.9 million payload-bound events; its
  isolated genesis audit remained CPU-bound for 90 minutes and hit the bounded
  verifier timeout;
- the five-minute realtime-feature worker inherited an expired source proof,
  began its own genesis scan, consumed approximately 9.7 GiB at peak, and was
  terminated by its 45-minute systemd limit before publishing a batch;
- the timer then started another identical feature attempt. This was safe
  because no unverified feature batch was published, but it was guaranteed
  zero-progress work and competed with ingestion for CPU, memory, and I/O.

The failure is operational, not an Alpha result. It neither reads outcomes nor
changes any research threshold.

## Ownership and proof flow

Full-chain work now has one owner: the resident public-data stream.

1. At startup the resident loads a checkpoint from the independently verified,
   append-only feature-batch authority.
2. A missing or expired proof starts a full audit in an isolated subprocess.
   A fresh proof starts from the exact sequence/hash checkpoint and verifies
   only the suffix.
3. The resident starts a new genesis audit four hours before the 24-hour proof
   expires. Source and cursor verifiers each retain a three-hour hard ceiling,
   leaving one hour of explicit scheduling margin.
4. The resident publishes the audit mode, exact verified frontier, full-audit
   time, batch-authority reference, and public-data-only safety declaration in
   its local status receipt. Incremental checks preserve the last successful
   full-audit provenance.
5. The short-lived feature worker never starts a genesis scan. When its batch
   checkpoint is absent or expired, it validates a fresh resident full-audit
   receipt and independently verifies only the local suffix after the attested
   frontier. The consumed status bytes are SHA-256-bound into the feature input
   evidence and the resulting append-only feature batch.

Unavailable, stale, future-dated, malformed, unsafe, invalid-chain, or
frontier-mismatched evidence fails closed before feature projection. The timer
may retry later, after the resident proof becomes fresh; retries do not weaken
the proof or manufacture a successful batch.

## Deliberate limits

- A genesis audit is still O(n). This change separates expensive integrity
  work from short jobs and schedules it early; it does not pretend that 45.9
  million rows are cheap.
- The resident status is a deployment-local attestation, not a cross-host
  signature. The feature batch binds its exact bytes and locally rechecks the
  suffix. A future segmented/Merkle authority can reduce O(n) renewal cost, but
  requires an independently reviewed migration and cannot be substituted by
  lengthening proof expiry or skipping payload validation.
- During initial or expired-proof recovery, public WebSocket ingestion may
  continue while downstream feature publication remains blocked.
- All data remains unauthenticated public market data. Credentials, account
  access, broker connectivity, order submission, testnet, and live trading
  remain unreachable; L2 and human-approval invariants are unchanged.

## Verification contract

Focused tests cover resident pre-expiry source renewal, cursor renewal,
resident-proof plus local-suffix consumption, absence of a feature-worker
genesis fallback, content-addressed periodic batches, and normal incremental
materialization. DGX acceptance additionally requires:

- immutable-release focused tests on ARM64;
- resident source and cursor full proofs completed within their ceilings;
- a succeeding feature batch using the resident-attestation basis;
- zero watchdog breach for the feature and stream channels;
- unchanged frozen Attention unit hashes and unchanged L2 environment flags.

