# Dynamic Correlation Drift Risk Scaling

## Decision

Add a deterministic, point-in-time correlation-regime monitor for strategy
books and wire its evidence into the existing `carry_crowd_risk_parity`
dry-run artifact.  Automatic gross scaling remains disabled because both
tested overlays failed the stricter executable control.  The monitor is
portfolio research infrastructure only: it does not authorize promotion,
deployment, order submission, broker access, or live trading.

The increment closes the local backlog left after the PIT regime classifier
and real monitoring snapshot were completed.  It does not change the machine
top-tier score because the remaining scored requirements are external,
time-based, or post-L2.

## Pure Contract

`services/portfolio/correlation_drift.py` owns the pure kernel.

Inputs:

- named, index-aligned book return sequences;
- an exclusive `end_index`, so later observations cannot affect an earlier
  decision;
- a frozen policy containing disjoint baseline/recent window lengths,
  correlation and increase thresholds, and WARN/BLOCK gross multipliers.

For every sorted book pair, the kernel computes Pearson correlation over:

1. baseline: `[end - recent_window - baseline_window, end - recent_window)`;
2. recent: `[end - recent_window, end)`; and
3. increase: `recent_correlation - baseline_correlation`.

Pair verdicts are:

- `BLOCK` only when recent positive correlation is at least the block
  threshold and the increase is at least the block-increase threshold;
- `WARN` when either warning threshold is crossed; otherwise `PASS`.

The overall verdict is the worst pair.  Insufficient/misaligned/non-finite
input, invalid policy, fewer than two books, or degenerate variance fails
closed to `BLOCK`.  Output contains no raw return series: only pair IDs,
window positions, correlations, deltas, reasons, and an advisory gross
recommendation.  The pure kernel does not decide whether that recommendation
is applied.

## Allocator And Driver Integration

`CapitalAllocatorConfig` gains an optional `correlation_drift_policy`.  When
absent, allocation behavior and output weights remain unchanged.  When
present, the allocator evaluates the exact cleaned return panel used by its
covariance/CVaR optimizer and returns the assessment plus a typed
`correlation_gross_recommendation`; normalized sleeve allocation still sums to
one.  That field is deliberately named as a recommendation rather than an
execution instruction.

`tools/dry_run_combo_riskparity_multi.py` enables the policy only after the
full disjoint 52-week baseline plus 8-week recent window exists.  It records
policy, status counts, the latest assessment, the advisory recommendation,
`scaled_periods=0`, and `activation=DISABLED_BY_EXECUTABLE_CONTROL`.  The
combined target weights remain unchanged and the applied multiplier is always
`1.00`.

The policy's `0.75` WARN and `0.50` BLOCK recommendations are explicitly
`UNCALIBRATED_PRINCIPLED`, not venue-calibrated production limits.  Historical
runs are immutable and are not rewritten.

This null activation is evidence-driven, not cosmetic:

- Unscaled same-sample executable control: RAW return about 193.1%, Sharpe
  0.6098, drawdown 55.77%; vol-target Sharpe 1.1173, drawdown 17.51%.
- Unconditional correlation scaling: RAW return 158.3%, Sharpe 0.5591,
  drawdown 55.77%; vol-target Sharpe 1.0227, drawdown 17.86%.
- A second, predeclared “WARN/BLOCK plus immediately-prior joint loss” overlay
  looked promising in a chronological 70/30 sleeve-return approximation, but
  failed the executable control: RAW return 187.2649%, Sharpe 0.6012, drawdown
  55.77%; vol-target Sharpe 1.0922, drawdown 17.46%.

The stress-conditioned overlay improved vol-target drawdown by only 0.05
percentage point while reducing return and Sharpe, so it is not activated.
No additional threshold search was performed after this result.

## PIT And Safety Boundaries

- The kernel has no IO, RNG, wall clock, network, credentials, execution, or
  trading imports.
- `end_index` is exclusive and tested against suffix append attacks.
- Baseline and recent windows never overlap.
- The driver supplies only returns before the decision period.
- Default allocator behavior remains byte/field compatible when the optional
  policy is absent.
- This is a dry-run research diagnostic, never a live or pre-trade permission.

## Acceptance

- synthetic low/stable correlation passes;
- a low-to-high positive correlation break blocks and recommends lower gross;
- moderate evidence warns;
- malformed or insufficient evidence fails closed;
- suffix observations cannot change an earlier decision;
- capital allocation exposes a typed advisory recommendation without changing
  normalized sleeve weights;
- the real carry/crowd dry-run records the assessment, explicit null result,
  and zero scaled periods without changing target gross;
- canonical `python scripts/validate.py` remains green.
