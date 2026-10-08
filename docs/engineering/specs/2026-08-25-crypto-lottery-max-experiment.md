# Crypto weekly MAX lottery-demand experiment v1

Date frozen: 2026-08-25  
Readiness: `L2_DRY_RUN_ONLY`  
Authority: historical research diagnostic only

## Conflicting direct-crypto priors

Grobys and Junttila, *Speculation and lottery-like demand in cryptocurrency
markets*, DOI `10.1016/j.intfin.2021.101289`, report that low prior-week MAX
outperforms high MAX over the following week. Li, Urquhart, Wang, and Zhang,
*MAX momentum in cryptocurrency markets*, DOI
`10.1016/j.irfa.2021.101829`, report the opposite high-MAX continuation sign.
Both are peer-reviewed direct-crypto priors. QUANTOS freezes the former as the
primary because its weekly formation and forecast horizon map directly to the
available daily PIT panel; the latter is an explicit competing-sign control.
It cannot rescue a rejected primary.

## Frozen translation

- Data: PIT daily close and base-unit volume from `crypto_funding.db`.
- Decision universe: top 60 by median dollar ADV over the previous 28
  completed daily rows; a nested top-30 universe is the investability control.
- Signal: maximum daily log return across the seven completed rows ending at
  `t-1`. The primary score is its negative, so the book is long low MAX and
  short high MAX.
- Rebalance and label: every seven rows, non-overlapping,
  `close[t+7]/close[t]-1`.
- Portfolio: dollar-neutral top/bottom 25%, target AUM USD 1,000,000.
- Base cost: 4 bps fee + 4 bps slippage + square-root impact coefficient 20.
- Pressure cost: 8 bps fee + 8 bps slippage + impact coefficient 40.
- No sign, window, horizon, universe, quantile, threshold, or cost scan.

Exactly five candidates enter the global trial population before any outcome
is evaluated:

1. low-MAX primary, top 60;
2. high-MAX competing-sign control, top 60;
3. ordinary prior-week cumulative-return reversal control, top 60;
4. deterministic fixed-hash placebo, top 60;
5. low-MAX investability control, top 30.

## Sample and terminal boundary

The first scheduled primary decision with fewer than 60 complete signal-time
names, or with more than two terminal labels in the same holding period, is the
exclusive sample stop. Later breadth recovery is never re-entered. A missing
`t+7` label followed by any later positive close is a non-terminal data gap and
rejects the family before registry mutation. A genuine terminal contract is
retained and exited at its final positive close inside `(t,t+7]`; no
post-decision tradable close rejects the family. A separate adverse-terminal
replay moves the exposed terminal name against either long or short direction
by 20%.

## Decision bars

The primary is only a historical diagnostic pass when every bar holds:

- immutable full evaluator evidence and evaluator verdict `PASS`;
- net Sharpe at least 1.0, positive net return, DSR at least 0.95, PBO at most
  0.25, and maximum drawdown at most 15%;
- final 20% chronological suffix Sharpe at least 0.80 and positive return (not
  claimed blind or independently locked);
- positive return under double cost/impact and adverse terminal stress;
- participation measured and no greater than 10%;
- primary Sharpe strictly beats the high-MAX, ordinary reversal, and fixed-hash
  controls;
- top-30 low-MAX Sharpe at least 0.50 and positive return.

A pass grants no forward, paper, Testnet, or live authority. It can only justify
a separate preregistered forward-shadow decision. A rejection is retained in
the global trial ledger and no competing sign or threshold may be adopted
afterward under this experiment identity.

## Safety invariants

The implementation is deterministic offline research. It imports no broker
SDK, reads no credentials, cannot submit orders, cannot mutate Alpha lifecycle
state, and cannot enable live flags. Mainnet remains structurally blocked and
human approval remains mandatory.

## Frozen DGX outcome

The only formal evaluation ran on immutable ARM64 release
`34d5792cacb514a4b8749b566de1216d11911596`. It registered the five frozen
candidates exactly once, moving the global trial population from 2,855 to
2,860 under search `search-e5af5b0fa7e757a5133dfda4d`. The dataset version
was `459742219157f0556518313ec279864611fb2e96dfb64fbdf9169f5b32e978234`
and the contract hash was
`78d1bc96c6d4378f7288920e1cdc64c4bc8acce99a869f18b7a84bfd2cef56751`.

The primary low-MAX book was rejected: net Sharpe `-0.2540`, net return
`-48.18%`, maximum drawdown `68.92%`, DSR `1.43e-10`, PBO `0.40`, and maximum
participation `18.79%`. Its final 61-period chronological suffix also failed
(Sharpe `-0.0119`, return `-1.81%`), and double cost/impact reduced total return
to `-64.10%`. The peer-reviewed high-MAX competing sign also lost money
(Sharpe `-0.1939`, return `-41.56%`), so the literature conflict did not reveal
an implementable sign in this market/data/cost contract. The top-30 low-MAX
control had only Sharpe `0.1926`; its `+7.43%` base return became `-23.38%`
under double cost/impact. It therefore cannot rescue the primary.

The decision is `REJECTED_HISTORICAL`. The immutable 6,507,040-byte artifact is
retained at
`/srv/quantos/state/evidence/crypto_weekly_lottery_max_34d5792.json` with
SHA-256
`d8ad393e9b3565efcc29b9717a5984490483d8eeab11505c59998df76f318eea`,
owner/group `root:quantos`, and mode `0440`. A deliberate overwrite attempt was
rejected before registry mutation; the trial population stayed at 2,860 and
the artifact hash was unchanged. This exact specification is now an
`EXISTING_FALSIFIED_IMPLEMENTATION`. No sign flip, cost reduction, threshold
change, forward admission, Testnet authority, or live authority follows from
the result.
