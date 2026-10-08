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
- Sample end: the first scheduled decision after sample start with fewer than
  60 complete signal-time names, or more than two terminal labels in the same
  primary holding period, is excluded. All later rows stay excluded even if
  breadth recovers. A singleton such as the observed XMR termination remains a
  forced exit; a large terminal cluster is systematic source right-censoring,
  not dozens of invented delistings. This boundary is computed before any
  return statistic; bad weeks are never skipped and the universe is not reduced
  after observing the DGX coverage failure.
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

## Frozen DGX result

The only formal historical run used immutable release
`b9e7b45a3c10378082525ecb285088b29a055014`, dataset version
`82d73dd26da563170d03083addda054523cc24775a562a993ee8e0092853b1e41`,
contract hash
`1ab230b058866e5121ab4112c98cef76433580809df29b928ccc64b04434aa8b0`,
and search id `search-fad56768528f37e68c49bba3`. The five frozen
candidates increased the global trial population from 2,850 to 2,855 exactly
once.

The primary had 305 non-overlapping weekly periods, net Sharpe `-0.3502`, net
return `-53.15%`, maximum drawdown `61.51%`, DSR approximately
`3.87e-11`, PBO `0.0857`, and maximum participation `17.19%`. Its final 20%
chronological suffix had Sharpe `-0.9716` and return `-15.92%`. It also failed
double-cost, terminal-event, sign-control, hash-placebo, total-volatility, and
top-30 investability bars. Full immutable evaluator evidence verified, but the
verdict was `FAIL`; the family status is therefore `REJECTED_HISTORICAL`, with
no forward, paper, testnet, or live admission.

The immutable DGX artifact is
`/srv/quantos/state/evidence/crypto_idiosyncratic_volatility_b9e7b45.json`
(`root:quantos`, mode `0440`, 6,476,013 bytes), SHA-256
`29bc75421162bdc16d3b349051f85dccdebe40dfa500a7a5db6fc736a004294c`.
An overwrite attempt was refused before registry mutation and the trial count
remained 2,855. This exact specification is closed to threshold relaxation,
sign rescue, or post-result window changes; any future IVOL work must state a
new economic mechanism and consume a new trial budget.
