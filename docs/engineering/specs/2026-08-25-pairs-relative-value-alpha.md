# Distance-selected pairs relative value: frozen experiment contract

Status: PRE-REGISTERED IN CODE, OUTCOME NOT ACCESSED

## Economic claim and boundary

Gatev, Goetzmann, and Rouwenhorst motivate a relative-value rule in which pairs
are selected on a formation sample and traded only in a disjoint sample.  This
is a cross-asset prior, not evidence that the same rule works in crypto.  The
experiment below tests the crypto translation once.  It is independent of the
already-rejected broad cross-sectional/residual mean-reversion family.

This driver is research-only.  It cannot promote an Alpha beyond the existing
non-human ceiling, cannot submit orders, and cannot make historical evidence
count as forward evidence.  A positive result is at most a historical research
finding and must enter a separately frozen forward paper track.

## Frozen inputs and transformations

- PIT source: Binance spot daily close and volume already stored locally.
- Formation window: 180 completed daily rows.  The sample starts at the earliest
  formation row with 24 complete eligible instruments; this readiness scan uses
  formation inputs only and never reads a trading return.
- Trading window: the next 90 completed daily returns.  Pairs remain fixed for
  the whole trading window; cycles do not overlap.
- Formation universe: instruments with complete positive close and non-negative
  volume throughout formation, ranked by formation-only median `close*volume`;
  retain 24 with deterministic instrument-id tie breaking.
- Selection: normalize every formation price path to 1.0 at its first row, rank
  all pairs by summed squared path distance, and greedily retain 10 disjoint
  pairs.  Pair orientation is lexical and therefore outcome-independent.
- Spread statistic: left normalized price minus right normalized price.  Mean
  and population standard deviation are estimated once from formation only.
- Daily signal: negative current spread z-score for convergence.
- Daily pair return label: `0.5 * (left_return - right_return)` from the signal
  close to the next close.
- Cost liquidity input: minimum current-day dollar volume of the two legs.  A
  missing current or next price fails closed; it is never silently dropped.
- Portfolio: cross-sectional long/short over synthetic pair instruments,
  quantile 0.30, daily annualization 365, target AUM USD 1 million, fee 4bps,
  slippage 2bps, square-root impact coefficient 10.

## Complete family, controls, and research budget

Exactly three candidates are registered together before evaluation:

1. `pairs_distance_convergence_v1`: distance-selected convergence, the primary
   hypothesis.
2. `pairs_distance_continuation_v1`: identical pairs and samples with the sign
   reversed, a direction falsifier.
3. `pairs_hash_convergence_v1`: convergence on disjoint pairs selected by a
   fixed SHA-256 ordering, a selection falsifier.

There are no threshold, window, universe-size, pair-count, quantile, or cost
variants.  The family budget is exactly three trials.  CSCV/PBO is computed on
the aligned three-candidate batch, while DSR uses the durable global experiment
catalog.  Signal prefix-invariance is machine checked before `leakage_ok=True`
is supplied; an unassessable proof remains fail-closed.

## Frozen decision rule

The primary is `HISTORICAL_DIAGNOSTIC_PASS` only if all conditions hold:

- evaluator verdict is `PASS`;
- net Sharpe is at least 1.0 and total net return is positive;
- DSR is at least 0.95 and PBO is at most 0.25;
- maximum drawdown is at most 15%;
- the final 20% of complete trading cycles has positive return and Sharpe at
  least 0.80;
- a doubled-base-cost replay remains profitable;
- convergence net Sharpe exceeds both the sign and hash controls.

Every other outcome is `REJECTED_HISTORICAL`.  Neither outcome authorizes live
trading.  The evidence artifact retains the contract hash, dataset hash, exact
trial hashes, catalog trial counts, evaluator evidence hashes, cycle selections,
cost replay, and every failed decision bar.

## Primary source

Evan Gatev, William Goetzmann, and K. Geert Rouwenhorst, *Pairs Trading:
Performance of a Relative-Value Arbitrage Rule*, NBER Working Paper 7032,
DOI 10.3386/w7032.
