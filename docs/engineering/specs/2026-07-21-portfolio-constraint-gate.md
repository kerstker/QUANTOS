# Unified Portfolio Constraint Gate

## Decision

Add one small, offline, fail-closed portfolio gate that composes the limits
already implemented as separate diagnostics. It evaluates empirical tail loss,
firm gross/net exposure and concentration, capacity bottlenecks, and crowding
evidence. The gate is diagnostic and load-bearing only when a caller explicitly
supplies `PortfolioConstraintInputs`; legacy callers remain unchanged.

## Contract

- `PASS` is the only runtime approval; `WARN`, `PENDING`, and `BLOCK` raise
  `PortfolioConstraintViolation` at an opted-in portfolio boundary.
- Missing requested evidence is `PENDING`, not an invented pass.
- Invalid values, malformed scopes, breached limits, non-finite scenarios, and
  insufficient coverage are `BLOCK`.
- Tail risk is empirical CVaR loss over caller-supplied return scenarios with
  explicit symbol order and alpha; no historical data is read implicitly.
- Results expose named checks, reasons, metrics, and provenance without any
  order, broker, credential, network, or live-trading surface.

## Runtime boundaries

The gate is available at the post-optimization target in `construction.py`, at
the constrained optimizer output, and at `signal_adapter.py`. The signal
adapter may derive position notionals from its caller-supplied AUM and ADV, but
never fabricates missing ADV. The post-rebalance check protects the actual
target after no-trade-band adjustment.

## Honest limit

This is an L2 research/dry-run control, not a venue-calibrated margin or
intraday risk service. It does not change top-tier assessment maturity by
itself; real calibrated limits and persisted operational risk state remain
separate work.
