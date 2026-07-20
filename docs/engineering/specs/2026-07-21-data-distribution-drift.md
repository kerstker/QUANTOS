# PIT Data Distribution Drift Diagnostic

## Decision

Add one small, read-only distribution-drift primitive and use it for the
console's real crypto dataset snapshots when those snapshots are available.
The primitive is diagnostic only: it never changes feature values, data
quality status, portfolio weights, promotion, or execution.

## Contract

`services/data/distribution_drift.py` compares two disjoint trailing windows
from one numeric series:

- baseline: `[end - recent_window - baseline_window, end - recent_window)`;
- recent: `[end - recent_window, end)`;
- `end_index` is exclusive, so appending future values cannot change an older
  assessment.

The score is the two-sample Kolmogorov-Smirnov distance. A frozen policy maps
the score to `PASS`, `WARN`, or `BLOCK`. Invalid policy, non-finite values,
misaligned windows, or insufficient observations fail closed to `BLOCK`.
The result retains window positions, sample counts, means, population
volatilities, score, reasons, provenance, and calibration marker; it never
returns the raw values.

Default policy is 60 baseline observations, 20 recent observations, WARN at
KS `0.20`, and BLOCK at KS `0.35`. These are explicit principled research
thresholds, not a production calibration or a trading trigger.

## Real read-only wiring

`DefaultDataConsolePort.enumerate_distribution_series()` reads close-to-close
returns from the existing bitemporal `crypto_daily.db` only. The monitoring
route adds `DATA_DISTRIBUTION_DRIFT` rows with real PIT provenance when the DB
exists. A clean checkout with no DB emits no fabricated distribution rows;
the pre-existing illustrative feature rows remain explicitly synthetic until
materialized feature observations exist.

## Safety and acceptance

- pure kernel has no IO, network, credential, broker, order, or runtime import;
- the data port is read-only and fail-closed on missing/unreadable stores;
- the monitoring route remains read-only and cannot authorize live trading;
- stable and shifted synthetic distributions, suffix invariance, malformed
  inputs, and real-port extraction are tested;
- the canonical validation gate remains green.
