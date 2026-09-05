# Causal order-block study — frozen before computation

User continuation authorizes research with reported maker 0 / taker 0.036%
(3.6bp). No account credentials queried. This is an explicitly specified new
hypothesis inspired by the supplied package, not its missing structure engine.
BTCUSDC perpetual, 4h UTC (resolve package's conflicting 1h DSL in favor of its
4h prose). Seen-history evaluation 2025-01-01 00:05 through 2026-08-01 00:05,
existing verified USDM minute trade/mark archives and actual funding settlements.
Historical venue rules remain a current-rule scenario, not formal admission.

## Fixed causal signal

Completed 4h OHLC bars only; incomplete bars reset state and need 100 consecutive
complete bars to trade. EMA100 seeded by first close, alpha=2/101; no trades
before 100 consecutive bars. A swing high/low is a strict extremum in 5 bars
(two left/two right), and becomes available only at close of the second right
bar. Update confirmed pivots at each close, each pivot can break only once.
Bullish BOS: close exceeds last confirmed, unbroken swing high; bearish mirrors.
Consume a broken pivot even if no FVG is present.
Bullish FVG at i: low[i] > high[i-2] and close[i-1] > open[i-1]. Bearish mirrors.
Create OB only when BOS and matching FVG occur at same completed bar. Select
latest opposite-color candle in i-12 through i-1; full low/high range. Confirmation
is close of i, never the selected earlier anchor candle's timestamp.
Existing blocks processed before creation of new blocks. Invalidate bullish block
if completed close falls below its low (bearish above high). Otherwise first
subsequent bar overlapping the range consumes the block, regardless of filters.
Trade that touch only if previous close was above bullish high/below bearish low,
and current close >/< EMA100. Multiple matching blocks give a single direction;
conflicting directions give no signal. No sample-length-dependent pool truncation,
no retrospective signals before confirmation. No age optimization.

## Execution and cost

Enter at next 4h boundary +5 minutes, using that minute's trade open as proxy
plus adverse 2/5/10bp slippage and tick rounding. All entries/exits are taker at
0.00036; maker zero recorded but not credited without queue/fill evidence.
Single position, no pyramiding, ignore all entry signals while held; no same-
time exit/reentry. Size once at entry: 50% current equity margin times 2x or 5x
=> 1.0x or 2.5x notional; quantity floor .001 BTC, minimum notional50, maximum
market size from frozen rules. Initial equity1500 USDC; costs/funding compound.

Fixed price stop 0.5% from actual simulated fill, no TP/trailing. Observe completed
1m high/low, and exit at following minute open plus adverse costs; never assume
fill at the stop price. Observe mark losses at minute opens and funding events.
Timeout 80 hours from actual entry, exit at next available minute open. 150 USDC
cumulative loss latches new-risk halt and exits next minute, with overshoot allowed.
Run both with/without account stop. Unsupported liquidation fails the case.
Hypothetical margin: initial1/leverage, maintenance10% notional, explicitly not a
historical Binance tier. No funding prediction from future settled rates.
Canonical Decimal ledger is the only cash/position/fee/funding implementation.

## Trial population and decision

Two leverages x three slippages x two account stops =12 strategy scenarios.
Cash and passive25 each with same three costs/two stops =12 baseline scenarios.
Register all24 trials before any evaluation, preserve every result and rerun.
No tune-after-results. Primary case 2x/5bp/with account stop. Further-review checks:
positive in both years and every 2x cost/stop case; >=20 completed trades;
minute mark drawdown <=10%; positive HAC market-adjusted alpha t>2.4; positive
PnL after removing best5 daily increments. Sharpe based on daily equity, not 4h
annualization error. Report trades, costs, funding, stop/timeout counts, annual
PnL and benchmark regressions. Formal DSR/PBO and untouched forward are absent;
none of these scenarios grants Paper/live authority or establishes profitability.
