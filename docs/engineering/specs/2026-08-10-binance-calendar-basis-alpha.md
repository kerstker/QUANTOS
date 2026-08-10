# Binance USD-M calendar-basis alpha: pre-registered research contract

Status: **PRE-REGISTERED / L2 RESEARCH ONLY**  
Date: 2026-08-10

## Objective

Test one fixed relative-value hypothesis that is executable entirely on Binance
USD-M Futures Testnet: the mark-price basis between a perpetual and the nearest
listed quarterly delivery contract converges over the following week.

This slice is not permission to promote an alpha, create an order, read a
credential, or start a Testnet soak. It first produces falsifiable historical
evidence. A separate formal-admission change is allowed only if the pressure
test below clears every pre-registered hurdle.

## Frozen candidate family

The family has exactly two members and no parameter scan:

1. `basis_convergence`: when quarterly basis is positive, long the perpetual
   and short the quarterly contract; reverse both legs when it is negative.
2. `basis_divergence_control`: the exact sign-reversed falsification control.

The universe is fixed to `BTCUSDT` and `ETHUSDT`. Each underlying contributes a
dollar-neutral perpetual/quarterly pair. Total gross exposure is 1.0, split
equally across the two pairs and the two sides of each pair.

## Causal schedule and roll rule

- Decision cadence: Monday 01:00 UTC, after the Monday 00:00--00:59 mark-price
  bars are complete.
- Signal: `quarter_mark / perpetual_mark - 1`, computed only from those
  completed bars.
- Holding interval: to the next Monday 01:00 UTC decision.
- Perpetual return: next decision mark return minus the exact 21 funding
  settlements paid by a long perpetual during the interval.
- Quarterly return: next decision mark return for the same contract.
- Contract selection: the nearest quarterly contract that has a completed mark
  at both decisions and delivers strictly after the next decision. A contract
  is never held through delivery. Changing contract ids is charged as real
  turnover.
- Missing pair, missing funding settlement, missing completed mark, expiry
  crossing, duplicate timestamp, malformed price, or non-finite value rejects
  that period; no value is imputed.

## Fixed realism and falsification hurdles

- Base round of turnover costs: 4 bps fee + 2 bps slippage per traded notional.
- Stress costs: exactly twice the base values.
- At least 104 non-overlapping weekly periods.
- Base-cost annualized Sharpe must exceed 2.70. This is a pressure-test floor,
  chosen before observing this return stream because the current catalog-wide
  trial population requires approximately that strength for DSR admission.
- Twice-cost cumulative return must be positive.
- Each of four contiguous chronological folds must have positive cumulative
  base-cost return.
- The convergence member must beat the divergence control on full-sample
  base-cost return.

Failure of any hurdle records a falsification result and stops the slice. It
must not create a new formal evaluator, register a catalog trial, generate a
Testnet intent, or change any validation threshold.

## Formal-admission condition

Only after all pressure hurdles pass may the platform add a paired
relative-value evaluator. That evaluator must retain the existing catalog-wide
DSR, replayable two-member PBO, leakage, sequential walk-forward, regime,
double-cost, catalog provenance, and immutable evaluation requirements. It must
represent paired-return evidence honestly; it may not fabricate cross-sectional
IC or lower `AlphaFactoryConfig.min_breadth` to fit a four-leg book.

## Safety boundary

The fetch and pressure-test paths use public market data only. They contain no
account endpoint, credential handling, broker adapter, mandate, order intent,
order submission, Mainnet execution, Micro-Live path, or real-money path.

