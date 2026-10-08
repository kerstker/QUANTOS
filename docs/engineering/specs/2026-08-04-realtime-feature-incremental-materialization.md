# Realtime Feature Incremental Materialization

Date: 2026-08-04

## Problem

The Binance public-stream feature worker is logically idempotent but not
operationally incremental.  Each cycle projects the latest 3,500 source rows
again and reads every retained value for every drift series.  At the current
5.2M-row feature store this means more than one million empirical KS windows
are recomputed before the realtime Alpha worker can consume the next batch.

That latency is incompatible with continuously operating paper research.  It
also couples an append-only historical archive to a bounded trailing health
diagnostic even though the diagnostic only consumes a current 60+20 window.

## Decision

Implement two independent, fail-closed watermarks:

1. **Projection watermark.**  For each instrument, use the minimum latest
   knowledge time across all eight materialized feature IDs.  Only source
   snapshots strictly newer than that watermark are projected.  Taking the
   minimum makes crash recovery safe: if any feature is absent or lags, the
   source snapshot is replayed and `INSERT OR IGNORE` repairs only the missing
   rows.  The feature batch insert remains one SQLite transaction.
2. **Bounded PIT drift history.**  Read the latest 240 observations per
   `(feature, version, instrument)` in canonical event/knowledge-time order.
   This keeps the held-out 60+20 assessment window and leaves five earlier
   calibration windows per series at stride 20.  With the production 98
   series this yields 1,960 deterministic permutation windows, above the
   required 100, while the dominant drift work remains constant as the archive
   grows.
3. **Trusted source-chain checkpoint.**  The source head `(seq,row_hash)` is
   committed into the existing append-only feature-batch hash chain.  A normal
   cycle validates only rows after that anchored head and reads its source
   window in the same SQLite snapshot.  Missing/malformed anchors trigger a
   full bootstrap; an anchor mismatch blocks.  Every 24 hours the checkpoint
   is deliberately ignored and the chain is revalidated from genesis, so
   incremental operation does not permanently hide historical corruption.

The append-only archive is not truncated.  Formal Alpha validation, forward
evidence, promotion, deployment, live trading and order submission are
unchanged.  Distribution drift remains diagnostic and non-load-bearing.

## Runtime evidence

The replaceable status artifact adds:

- projection watermark policy and per-instrument watermarks;
- source snapshot counts split into eligible and already-materialized rows;
- bounded history policy, loaded observations and calibration windows;
- cycle elapsed time for operational latency monitoring.

The append-only batch journal remains source-bound and therefore excludes
state-dependent incremental counts.  It records the stable bounded-history
limit and calibrated source identity, so replaying the same source digest
after a crash remains byte-identical instead of conflicting with the existing
batch ID.

No wall-clock value enters feature identity, Alpha evaluation, or trading
decisions; elapsed time is status-only observability.

## Acceptance

- repeated cycles append no duplicate feature rows or batch events;
- a lagging feature forces replay from the minimum watermark;
- bounded tail reads preserve PIT truncation and canonical ordering;
- calibration remains READY and structurally complete;
- actual 5.2M-row-store execution completes with bounded drift input;
- realtime Alpha discovery and engineering paper continue after deployment;
- all private, broker, live and order capabilities remain false.

## Verified production-state result

The first checkpoint bootstrap completed in about 87 seconds.  The immediately
following cycle validated 280 new source rows from trusted sequence 814,310,
projected 2,240 new feature values, skipped 3,220 already-materialized source
snapshots, and completed internally in 2,192ms.  Drift consumed exactly 23,520
observations and 1,960 calibration windows; the feature batch chain remained
valid.  The retained feature archive contained 5,302,050 rows after that cycle.
