# Crypto idiosyncratic-volatility experiment v1

Date frozen: 2026-08-25  
Readiness: `L2_DRY_RUN_ONLY`  
Authority: historical research diagnostic only

## Why this family

Zhang and Li, *Is idiosyncratic volatility priced in cryptocurrency markets?*,
Research in International Business and Finance 54 (2020),
DOI `10.1016/j.ribaf.2020.101252`, reports a positive cross-sectional relation
between idiosyncratic volatility and subsequent cryptocurrency returns. This is
direct crypto evidence and is economically distinct from QUANTOS' already
tested momentum, carry, positioning-reversal, relative-value, order-flow, and
liquidity families. The paper is a prior, not a profitability guarantee.

## Frozen translation

- Data: PIT reads from `crypto_funding.db`; daily close and base-unit volume.
- Decision universe: top 60 by median dollar ADV over the previous 28 completed
  daily rows. The investability control uses the nested top 30.
- Signal window: 28 completed daily returns ending at `t-1`; row `t` is used
  only to prove entry availability.
- Market factor: equal-weight daily return of the decision-time liquid universe.
- Signal: population standard deviation of residuals from per-asset OLS with an
  intercept and the market factor. High IVOL is the paper-directed primary.
- Rebalance/label: every seven rows, non-overlapping, `close[t+7]/close[t]-1`.
- Portfolio: dollar-neutral top/bottom 25%, target AUM USD 1,000,000.
- Base cost: 4 bps fee + 4 bps slippage + square-root impact coefficient 20.
- Pressure cost: 8 bps fee + 8 bps slippage + impact coefficient 40.
- There is no window, horizon, universe, quantile, threshold, or cost scan.

Exactly five candidates are charged to the global trial population before any
evaluation:

1. high residual volatility primary, top 60;
2. exact low-IVOL sign control, top 60;
3. total-volatility control, top 60;
4. deterministic fixed-hash placebo, top 60;
5. high residual volatility investability control, top 30.

## Delisting and missing labels

No asset is deleted because it will delist. If `t+7` is missing and any later
positive close exists, the observation is a non-terminal data gap and the whole
family is rejected before the experiment registry opens. If there is no later
positive close, the contract is retained and forcibly exited at the final
positive close inside `(t,t+7]`. If no post-decision trade exists, the family is
rejected.

The base result uses that forced exit. A separately frozen pressure replay adds
an adverse 20% asset return to the exposed terminal name: subtract 20% for a
long and add 20% for a short, so both directions lose rather than allowing the
short book to benefit mechanically from delisting.

## Decision bars

The primary is only a historical diagnostic pass when every bar holds:

- immutable full evaluator evidence and evaluator verdict `PASS`;
- net Sharpe at least 1.0, positive net return, DSR at least 0.95, PBO at most
  0.25, maximum drawdown at most 15%;
- final 20% chronological suffix diagnostic Sharpe at least 0.80 and positive
  return (the suffix is explicitly not claimed blind or independently locked);
- positive return under double cost/impact and terminal-event stress;
- participation measured and no greater than 10%;
- Sharpe strictly beats sign, hash, and total-volatility controls;
- top-30 control Sharpe at least 0.50 and positive return.

A historical pass grants no forward, paper, testnet, or live authority. It can
only justify a separate preregistered forward-shadow decision. A rejection is
retained in the global trial ledger and its thresholds are not relaxed.

## Safety invariants

The implementation is offline and deterministic. It imports no broker SDK,
does not read credentials, cannot submit orders, cannot mutate Alpha lifecycle
state, and cannot enable live flags. Mainnet remains structurally blocked and
human approval remains mandatory.
