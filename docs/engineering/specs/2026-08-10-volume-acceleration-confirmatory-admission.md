# Volume-acceleration finalist family: confirmatory formal admission

Status: **PRE-REGISTERED / NOT YET EVALUATED**  
Date: 2026-08-10

## Prior evidence and purpose

The production experiment authority contains a completed deterministic factory
search (`search-36ffa5e2daf9f2a6f6ab8dc5`) whose strongest evaluated finalist was:

`cs_zscore(cs_scale(ts_delta(ts_delta(volume, 30), 7)))`

Its stored evaluations have catalog-wide DSR around 0.98 and net daily Sharpe
around 1.10, but they predate mandatory PBO and regime evidence and therefore
correctly remain `REQUIRES_HUMAN_REVIEW`. This experiment does not invent a new
winner. It freezes the 14 finalists that received immutable evaluations in the
source search, replays them together on the current PIT panel, and supplies the
missing evidence through the production evaluator.

## Frozen family and selected subject

- Population: exactly the 14 evaluated volume-family finalists linked to the
  source search. Their expressions are literal constants in the driver.
- Selected subject before replay: the exact 30-day then 7-day volume
  acceleration expression above.
- Horizon: one day, non-overlapping daily decisions.
- Calendar: exact intersection of causal `event_time` values across all 14
  definitions; no finalist may use a private warm-up calendar.
- Universe: existing crypto OI-major PIT panel, but the definitions consume
  only the `volume` field and next-day close return.
- Book: top/bottom 30% dollar-neutral cross-section.
- Annualization: 252, matching the source factory evaluation contract.
- Cost: fixed 4 bps fee + 2 bps slippage per changed notional; no uncalibrated
  market impact is claimed.
- Research budget: exactly 14 trials, one feature, two parameter keys. No
  expression, window, horizon, quantile, universe, or cost scan.

The source search and every preceding experiment remain in the global trial
population. The new confirmatory definitions include this experiment contract
in their content hashes and count as new trials; nothing is overwritten or
retroactively relabelled.

## Required pass

The selected subject must receive a verified production `PASS` with:

- catalog-wide replayable DSR;
- replayable 14-member CSCV/PBO evidence;
- computed expression leakage evidence;
- computed regime profitability;
- sequential walk-forward persistence;
- coverage, cost, redundancy, dataset, code, search, and content bindings.

Any missing/invalid evidence, timeout, 429, empty result, PBO failure, regime
failure, `FAIL`, or `REQUIRES_HUMAN_REVIEW` blocks admission. A historical
`PASS` permits only the separate forward-paper admission workflow. It does not
authorize Testnet intent, credential access, execution startup, Mainnet,
Micro-Live, real capital, or order submission.

