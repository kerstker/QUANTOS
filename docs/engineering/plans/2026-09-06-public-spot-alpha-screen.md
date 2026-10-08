# Public spot Alpha screen — fixed research specification

Frozen before observing candidate results on 2026-09-06. User specification:
TSMOM_28D_LONG_CASH, BREAKOUT_55_20_LONG_CASH, RIDGE_PRICE_VOLUME_DAILY.
This is a separate spot cash account; existing USDT/USDC perpetual research is
preserved. No live orders, promotion, or economic authority.

## Scope and evidence

BTCUSDT first, ETHUSDT independent market replication; additionally BTCUSDC and
ETHUSDC as separate spot cases under the stated scope assumption. Keep every
cost/risk/market case, including failures.
Official Binance spot minute archives, checksum verified, with millisecond to
microsecond transition at 2025-01-01. Archive revisions and retrospective
availability remain unknown. Historical UTC bars are assumed available after
completion; this is an execution-proxy scenario, not recorded historical quotes.

Data window: 2022-11-01 through 2026-07-31, plus the first minutes of August 1
for the last valuation. Evaluation execution dates: 2025-01-01 through
2026-07-31 inclusive. August monthly archives are not yet published (404).
Training uses earlier data only. These dates are fixed for coverage reasons,
not selected from profitability. Previously discussed BTC history is seen;
no part of this retrospective screen is advertised as untouched OOS. ETH is
market replication, not an alternative winner selected from six candidates.

The user explicitly authorized frozen-current-rule historical scenarios when
historical rule epochs are unavailable. Capture current public exchangeInfo,
retain its hash/time, and label rule scope CURRENT_RULE_SCENARIO_ONLY. Missing
or unsupported current filters block a case; current rules do not establish
historical venue compliance. Fees are assumed quote-currency 10 bp per side;
adverse slippage 2/5/10 bp per side, all reported. No account credentials used.

## Fixed mechanics

Completed UTC daily OHLCV, derived from verified minute bars. Next execution
is 00:05 UTC, using that actual minute's open plus adverse slippage; current
minute volume/high/low/close are never used to fill at its open. Observe loss
at minute opens, exit at the next available minute open. No intraminute path
or guaranteed stop-price claim. Reject gaps before evaluating a case.

Initial quote cash 1500. Targets 25% or zero, daily rebalanced, rounded down to
quantity step; enforce current minimum/maximum quantity and notional filters.
Never short or borrow. Fees charged on executed notional. Insufficient-size
orders are skipped and recorded; residual positions remain marked. The 150
loss threshold includes marked holdings and fees and permanently blocks new
risk; exits may overshoot or leave untradeable dust. Also run without the
account stop. Benchmarks: cash, daily 25% passive target, 100% initial buy/hold
with fee-affordable quantity. Do not force liquidation at the final mark.

A uses the fixed 28-day close return. B compares today's close with previous
55/20 closes excluding today; actual position determines entry/exit branch,
target state is recorded independently. C uses the seven supplied features;
sample standard deviation (ddof=1), trailing 28 log-volumes, zero variance
maps to zero z-score. Nonpositive volume or missing/nonfinite input makes a
sample invalid; no forward filling. Standardization is training-only.

C retrains after Sunday close on exactly the latest 730 mature valid labels;
label exit reference must precede model fitting. Fixed Ridge alpha=1, intercept,
Cholesky solver, single numeric thread. Activate after measured fit completion,
skip a planned execution if fitting exceeds its five-minute budget. The
historical computation-time projection is a model assumption, not a recorded
historical training event. Entry threshold = 2*(fee+slippage)+5 bp; held exit
prediction <= 0. Missing features/model block new risk; positions stay recorded.

All runs and reruns use the append-only trial ledger in an isolated output
directory, with source/data/spec/rules/cost/risk identities. Completion of a
technical computation is not passage of statistical or promotion gates.
Report daily net return/drawdown/volatility, minute-observed drawdown, exposure,
turnover/costs/fills/holding episodes, annual subperiods, and matched benchmark
increments. ML calibration uses mature execution-to-execution labels only.
No parameter search, cost selection, carry, or LightGBM in this batch.

## Prior evidence

2026-09-05 BTCUSDC perpetual screen: 45 cases, all three candidate families
failed the registered screen. The formulas, account semantics and costs differ
from this specification and cannot be relabelled as these spot candidates.
The checked-in alpha trial snapshot uses an older evidence schema; inspect it
read-only, do not migrate or overwrite it to fit the current TrialLedger API.
