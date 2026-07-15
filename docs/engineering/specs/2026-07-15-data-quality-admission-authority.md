# Data-quality admission authority and canonical event

Date: 2026-07-15

Status: implemented and locally exercised under `L2_DRY_RUN_ONLY`.

## Problem

`tools/data_quality_admission.py` already recomputed a strict crypto-forward
quality and raw-lineage decision, but its output was an overwriteable JSON file.
There was no durable authority proving which evidence was admitted, no atomic
event outbox, and the weekly forward-track workflow did not require that
decision. An independently emitted event would have made the problem worse: the
event and the actual admission verdict could diverge.

## Implemented boundary

`services/data/admission_store.py::DataQualityAdmissionStore` is the local
single-node authority for these decisions. It stores the complete canonical
evidence document and independently validates:

- the fixed `L2_DRY_RUN_ONLY` readiness and forward-admission purpose;
- positive schema version and non-negative caller-supplied timestamps;
- sorted, unique, non-empty instrument scope;
- `PASS/BLOCK` consistency across quality, lineage, price basis, and reasons;
- the complete evidence SHA-256 and a separate decision SHA-256.

Each immutable decision and its redacted canonical event are inserted by the
same `BEGIN IMMEDIATE` transaction. The decision chain and dispatch-ack chain
are append-only SHA-256 chains protected by SQLite `UPDATE/DELETE` triggers.
Opening, appending, reading, or dispatching re-verifies all retained evidence,
event semantics, causal metadata, and both chains.

The new strict event is:

```text
data.quality.admission.decided v1.0.0
```

It contains only admission IDs, verdicts, counts, schema/readiness markers, and
SHA-256 commitments. Per-instrument errors, paths, source payloads, and block
reason text remain in the authority and are not copied into the shared bus or
console.

## Load-bearing flow

`run_data_quality_admission` now evaluates once, commits the decision/outbox,
and only then atomically replaces the read artifact. The weekly
`forward_track_advance` flow requires both its existing refresh checks and the
authoritative admission verdict to pass. Authority failure or a `BLOCK` verdict
keeps decision recording and realization paused and exposes only a redacted
status summary.

The seventh outbox owner is included in `domain_event_outbox_drain`; recovery
uses `publish_once` followed by an immutable acknowledgement, so a crash after
publish and before acknowledgement is replay-safe. The shared projection and
console `/events` whitelist the redacted event. The new authority database is a
required source in `tools/l2_backup.py`.

## Real local result

The 2026-07-15 read-only evaluation retained the existing honest conclusion:

- scope: 24 crypto instruments;
- quality verdict: `BLOCK`;
- lineage verdict: `BLOCK`;
- overall verdict: `BLOCK`;
- admission ID: `data-admission-8bb26fcafd7e6672ec8d66ceb6fcfff5`.

Recovery dispatch published and projected the one redacted event, acknowledged
the source outbox, and left zero pending rows. The source and projection chains
both verified. Overall event operations remain `PARTIAL` because three other
owner authorities are not enabled; this increment does not relabel them.

## Honest limits

This closes a local atomicity and replay gap. It does not restore Binance
funding availability, provide licensed market data, create external WORM or
object-lock retention, authenticate worker identity, replicate SQLite, provide
cross-host consensus, or authorize live trading. The complete data admission
contract currently covers the crypto forward evidence workflow only; FX,
macro, equities, and market-depth feeds still need equivalent source-specific
contracts before they can be load-bearing.

## Validation

The final unified gate passed all 18 stages: 2353 main tests passed with one
expected skip, 971 independent live/trading/tools tests passed, frontend
typecheck/68 tests/production build passed, and the governance scanner reported
312 files with zero violations. The machine assessment remained
`NOT_TOP_TIER` at 15/22 requirements passed.
