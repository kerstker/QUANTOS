# PIT Feature Distribution Drift

## Decision

Add a small, offline diagnostic that applies the existing trailing-window KS
kernel to a declared feature universe. The caller supplies feature values that
were already materialized or read with one `as_of` boundary; the diagnostic
does not open the feature store, infer missing values, or modify any trading
decision.

## Contract

- `PASS`, `WARN`, and `BLOCK` are preserved per feature and in the aggregate.
- Missing or unexpected feature IDs are `BLOCK`; a partial cache cannot look
  healthy.
- Invalid values, insufficient history, and invalid windows remain fail-closed
  through the existing one-dimensional kernel.
- The report is deterministic, exposes each feature assessment, and is marked
  `PIT_FEATURE_TRAILING_KS_DRIFT_V1` / `UNCALIBRATED_PRINCIPLED`.

## Boundary

This is a read-only L2 diagnostic. It does not persist alerts, change feature
values, scale positions, gate promotion, or add network, credential, broker,
order, or live-trading access. Filling the feature-value store and wiring a
real scheduled monitor remain separate work requiring operational evidence.
