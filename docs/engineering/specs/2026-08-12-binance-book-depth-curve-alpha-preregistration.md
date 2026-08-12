# Binance USD-M book-depth curve Alpha: pre-registration

Status: **PRE-REGISTERED / NOT YET EVALUATED**  
Date: 2026-08-12

## Hypothesis and causal boundary

The primary hypothesis is fixed before historical Alpha results are computed:
cross-sectional conservative near-book notional imbalance at `+/-0.2%` predicts
four-hour continuation.  A larger bid-side p10 notional curve relative to the
ask side ranks long; the opposite ranks short.

Input is the checksum-verified
`binance_usdm_book_depth_curve_hourly_v2` contract.  Each hour must contain a
complete structural book curve after a bounded, explicitly reported snapshot
quarantine and must independently align within 1% to the median of all 60
checksum-verified Binance USD-M 1-minute mark closes for that hour.  The 1%
bound is `UNCALIBRATED_PRINCIPLED`, not an empirical claim.

Historical Data Vision files are retrospective deliveries.  Their exchange
event times are retained, but their QUANTOS knowledge time is the actual fetch
time.  Backfilled history can support historical association and formal
historical validation; it can never receive retroactive forward credit.

## Frozen experiment

- Universe: exactly the 14 symbols in
  `config/binance_book_depth_alpha_preregistration.json`.
- History: latest common 90 completed archive days available on the first run.
- Decisions: UTC hour ends divisible by four; four-hour, non-overlapping return.
- Book: equal-weight top three long and bottom three short, gross exposure 1.
- Price: the bound independent hourly mark-price reference at decision and
  realization time.
- Cost: 6 bps times actual one-way portfolio turnover, including initial
  establishment; double-cost stress is 12 bps.
- Split: chronological first two thirds development report, final third locked
  OOS.  No threshold or definition may be changed after seeing the locked data.
- Annualization: 2,190 four-hour periods per 365-day year.
- Population: exactly six frozen curve definitions.  Every one counts as a
  trial.  The primary subject was selected before evaluation and a stronger
  secondary result cannot replace it without a new independent registration.

The five secondary definitions test 1% and 5% imbalance, near-minus-far
imbalance, bid/ask near-concentration skew, and mean curve imbalance.  Their
purpose is family breadth, redundancy and multiple-testing evidence, not
post-hoc winner substitution.

## Diagnostic pass and formal boundary

The primary must satisfy every machine-readable gate in the registration:
locked net Sharpe at least 1.0, positive mean rank IC with t-stat at least 2.0,
positive net return in at least two of three chronological locked subperiods,
positive double-cost net Sharpe, and no single instrument contributing more
than 35% of gross absolute PnL.

A diagnostic pass is only permission to run the existing production validation
DAG with catalog-wide trial accounting, DSR, CSCV/PBO, leakage, regime,
sequential walk-forward, cost and provenance gates.  Missing data, an empty
result, timeout, 429, invalid checksum, quality failure, or any non-PASS gate is
a block.  Even a formal historical PASS authorizes only a separately isolated
forward Shadow track.  It does not authorize Testnet intent, credentials,
broker connectivity, execution startup, Mainnet, Micro-Live, real capital or
order submission.
