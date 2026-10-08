# Licensed vendor-data admission — L2 readiness design

Date: 2026-07-28  
Capability: `DATA.VENDOR.DEPTH`  
Scope: licensed tick/order-book contract and delivery admission

## Problem

QUANTOS already had PIT storage, data-quality checks, a channel catalog and
refresh-history evidence.  It did not have one load-bearing decision that joined
the legal-use grant to measurable vendor delivery obligations.  A technically
valid file could therefore not prove that its use was licensed or that tick/book
coverage met a contractual SLA.

## Contracts

`quantos/contracts/vendor_data.py` defines immutable values for:

- the license grant, validity interval, licensed uses and retained contract
  evidence reference;
- the dataset kind and service-level contract;
- one content-addressed delivery and its PIT, schema, sequence, coverage and
  depth evidence;
- platform minimums which a vendor contract may tighten but never weaken.

## Admission

`services/data/vendor_admission.py::evaluate_vendor_delivery` evaluates all
applicable failures in deterministic order:

- vendor/dataset identity and licensed use;
- license validity and external-verifier result;
- no future delivery or future knowledge time;
- delivery lag and retained history span;
- expected-row coverage, missingness and duplicates;
- out-of-order rows and sequence gaps;
- required fields, instrument breadth and order-book depth.

Effective thresholds always choose the stricter value between the vendor
contract and platform policy.  Failure or absence of the verifier denies
admission.  L2 also rejects `LIVE` use even if an input grant claims otherwise.

`admit_and_archive_vendor_delivery` is the load-bearing write path.  It
canonicalizes the exact delivered rows, verifies the declared row count and
SHA-256, evaluates the license/SLA decision, and only then calls the immutable
raw archive.  Denied, count-mismatched or content-tampered batches cause zero
archive writes.  Accepted retries are content-addressed and idempotent, and the
archive sidecar records the grant/contract/delivery IDs without contract secrets.

## Delivery-history authority

`services/data/vendor_delivery_history.py` retains every terminal denial and
every accepted archive lifecycle in a strict append-only SQLite hash chain.
The canonical archive entrypoint now requires a `VendorDeliveryHistoryStore`;
callers cannot silently opt out.

Accepted deliveries use two immutable events:

1. `ARCHIVE_PENDING` commits before the raw archive write;
2. `ADMITTED` commits only after the archive returns the exact declared content
   SHA-256.

If the process or archive fails between them, the pending delivery remains
observable through `open_deliveries()`.  Exact replay is idempotent and can
append the missing terminal event.  A denied delivery appends `DENIED` with
canonical reason codes and no archive claim.  The authority also binds digests
of the exact grant, contract and platform policy, so changing license terms
under an existing delivery event conflicts rather than rewriting history.

The store enforces WAL, `synchronous=FULL`, strict schema and trigger sets,
UPDATE/DELETE denial, contiguous event sequence, global-head CAS, non-decreasing
authority time, deterministic lifecycle transitions and a SHA-256 chain.
Injected schema, removed triggers, truncation, payload mutation, stale-head
writes and clock rollback fail closed on append or reopen.
`data/cache/vendor_delivery_history.db` is also an optional L2 backup-contract
source: before genuine operation it is reported missing rather than fabricated;
once provisioned, the encrypted isolated-restore path automatically includes
and verifies it.

## Trust boundary

The code does not obtain or interpret a legal contract and contains no active
vendor grant.  `VendorLicenseVerifier` is an external trust seam; the tracked L2
default denies every grant.  An in-process fake verifier used in unit tests is
not external evidence and cannot raise the top-tier requirement to PASS.

The admission module performs no network I/O, carries no data/vendor credential,
imports no cloud, broker or market-data SDK and cannot place an order.

## Verification and maturity

Behavior tests cover compliant research admission, unavailable/rejected
verifiers, structural LIVE denial, stricter-threshold composition, identity and
schema mismatch, sequence/PIT failures, expiry, contract validation, exact
content/row-count binding, denial-before-write and idempotent immutable archive
publication.  Delivery-history tests cover two-phase archive recovery, accepted
and denied evidence, exact replay, changed-license conflict, CAS, clock rollback,
append-only enforcement, tamper/truncation and schema/trigger drift.
Governance tests pin the no-network/no-SDK boundary and default-deny behavior.

This remains M2 local readiness because the authority contains no genuine
vendor operations merely by existing.  M4 still requires a genuine licensed
source, independently verifiable contract authority, retained invoices/rights,
real tick/order-book deliveries, longitudinal authority history,
incident/remediation records and legal review.
