# Cross-sectional liquidity premium: frozen experiment contract

Status: DGX PREFLIGHT REJECTED_DATA_INTEGRITY, 2026-08-25

## Economic claim and boundary

Bekaert, Harvey, and Lundblad provide an emerging-equity prior that illiquidity
can be priced. Their evidence is not crypto evidence, and their equity liquidity
measure is not silently equated with exchange volume. This experiment makes one
explicit crypto translation: trailing Binance quote notional is an execution-
liquidity proxy, and the test asks whether the relatively less liquid portion of
an already-investable universe earns a premium after the very costs that could
make the apparent premium untradeable.

This driver is historical research only. A positive result cannot promote an
Alpha, open a paper track, connect a broker, submit an order, or relax any L2 or
human gate. It would only justify a separately frozen forward experiment.

## Frozen data, calendar, signal, and label

- PIT source: the existing Binance daily close/base-volume panel in
  `crypto_funding.db`; quote notional is `close * base_volume`.
- Decision liquidity window: 28 completed rows `[t-28, t)`. Decision-day volume
  is excluded, so an order at close `t` never assumes the completed volume of
  the same execution day was known in advance.
- Sample start: the earliest decision with 48 complete trailing-liquidity names.
  This readiness scan uses no future return.
- Rebalance and label: every seven rows with no skipped decisions; label is
  `close[t+7] / close[t] - 1`, creating non-overlapping weekly observations.
- Primary investable universe: the 48 names with highest prior-28-row median
  dollar ADV, requiring complete positive trailing prices/volume and an
  executable current close. The signal is `-log(trailing_median_ADV)`, so the
  long leg is relatively less liquid only inside the top-48 universe, not the
  untradeable tail of all listed assets.
- A missing selected future price fails the experiment. A prior holding that
  leaves the current cross-section must have current liquidation ADV; its exit
  pays base cost, square-root impact, and participation. Missing exit ADV also
  fails closed rather than allowing delisting or dynamic-universe leakage.
- Portfolio: dollar-neutral tails, quantile 0.25, weekly annualization 52,
  target AUM USD 1 million.
- Base costs: 4bps fee, 4bps slippage, square-root impact coefficient 20.
- Stress costs: 8bps fee, 8bps slippage, impact coefficient 40.
- Hard diagnostic participation ceiling: maximum exact per-name traded
  notional divided by trailing ADV must be at most 10% in every period.

## Complete family and research budget

Exactly four candidates enter the durable catalog together before evaluation:

1. `liquidity_illiquidity_premium_v1`: top-48 negative-log-ADV primary.
2. `liquidity_liquid_sign_control_v1`: exact sign reversal on the same panel.
3. `liquidity_hash_placebo_v1`: fixed salted SHA-256 scores on the same panel.
4. `liquidity_large_cap_illiquidity_v1`: the same primary signal restricted to
   the trailing-ADV top 24, testing whether the claim survives a more investable
   subset.

There are no lookback, horizon, universe-size, quantile, AUM, cost, or impact
variants. The family budget is exactly four trials. PBO is computed on the
aligned batch; DSR uses the durable alpha-hypothesis catalog. Prefix invariance
is machine checked per signal/universe path before `leakage_ok=True` can be
supplied.

## Frozen decision rule

The primary is `HISTORICAL_DIAGNOSTIC_PASS` only when every condition holds:

- immutable evaluation content and full DSR/PBO evidence both verify;
- evaluator verdict is `PASS`;
- base-cost net Sharpe is at least 1.0 and cumulative net return is positive;
- DSR is at least 0.95 and PBO is at most 0.25;
- maximum drawdown is at most 15%;
- the final 20% locked slice has net Sharpe at least 0.80 and positive return;
- the doubled-cost-and-impact replay remains profitable;
- participation is fully assessed and never exceeds 10%;
- primary Sharpe exceeds both the sign and hash controls;
- the top-24 investability control has Sharpe at least 0.50 and positive return.

Every other outcome is `REJECTED_HISTORICAL`. A positive historical outcome is
still not forward evidence and grants no testnet or live authority.

## Observed preflight result

The contract was frozen and fully validated at source commit
`2f231c84707e51d4a533f1d6dd508c9dd31b8de2`. On the DGX, construction stopped
before the experiment registry opened because selected instrument `XMR` lacked
its required `t+7` price (decision `2024-02-15T00:00:00Z`, required label
`2024-02-22T00:00:00Z`). The durable trial table remained at 2,850 rows,
`LIQUIDITY_PREMIUM_V1` had zero search events, and no return statistics were
computed. This is `REJECTED_DATA_INTEGRITY`, not evidence for or against the
economic premium.

The audited recorder commit is
`3e48132c42a1e0c534123b091ef04b0f9afd086c`. It wrote preflight record
`preflight-651a5cc899a6aad34b7501bc` for dataset version
`b82813352bba302fc95a6ab17f5968e6aaa5592b017c304259b76b6d122d89ecf` to DGX
artifact `evidence/liquidity_premium_2f231c84707e51d4a533f1d6dd508c9dd31b8de2.json`.
The artifact SHA-256 is
`e5d0ae51ee62b84ac0b14384e809c13cbcfa3b81dd03f716479107d1900a05b2`.

The failure is economically material: dropping XMR after seeing the missing
label, changing the top-48 universe, skipping that week, or imputing a zero
return would manufacture exactly the survivorship/untradeable-premium illusion
the experiment was designed to catch. None is allowed. The audited recorder
published this pre-registry rejection with the original contract/source commit
and a separate recorder commit, while performing no registry mutation. This
frozen family is closed; any delisting-aware payoff convention would be a new,
independently pre-registered experiment rather than a repair of this result.

## Primary source

Geert Bekaert, Campbell Harvey, and Christian Lundblad, *Liquidity and Expected
Returns: Lessons from Emerging Markets*, Review of Financial Studies 20(6),
DOI 10.1093/rfs/hhm030.
