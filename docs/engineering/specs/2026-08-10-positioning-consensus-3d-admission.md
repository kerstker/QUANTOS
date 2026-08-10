# Three-day positioning-consensus formal admission

Status: **PRE-REGISTERED SHARED-CALENDAR CORRECTION / NOT YET EVALUATED**  
Date: 2026-08-10

## Why this is a new counted hypothesis

The existing seven-day positioning-consensus candidate is the closest current
formal candidate, but it fails the immutable catalog-wide DSR threshold. The
Binance Data Vision history cannot honestly extend it backward: top-trader
fields are blank across large parts of 2022 even though price and retail-ratio
history exist. No interpolation or proxy substitution is allowed.

This slice therefore makes one new temporal hypothesis and counts it as new
research: a confirmed retail/top-trader positioning extreme is primarily a
short-lived unwind, so a fixed three-day non-overlapping hold captures more of
the event before the cross-section mean-reverts. It is not a rerun of the
seven-day definition and it does not alter any validation threshold.

## Frozen family

Exactly three definitions enter the append-only trial population before any
backtest result is observed:

1. canonical retail-positioning contrarian control;
2. canonical top-trader-positioning contrarian control;
3. the same fixed equal-standardized retail/top consensus expression used by
   the seven-day candidate.

All three use a three-day forward horizon, non-overlapping decision periods,
the existing `crypto_oi_majors_pit` universe, the existing realistic panel
cost model, and `periods_per_year = 122`. There is no horizon, quantile,
expression, universe, or cost scan in this experiment.

The family calendar is common by construction: first build every candidate on
all causal daily rows, intersect exact `event_time` values across all three,
then retain the first common row and each later common row at least 72 hours
after the previous decision. Missing top-trader blocks therefore create one
shared gap; they cannot independently reset each candidate's three-day phase.

An initial implementation incorrectly applied non-overlap separately to each
candidate. The real 2022 top-trader gaps left only two exact timestamps common
to all three, so PBO failed closed as `OVERFIT_NOT_ASSESSED`. That immutable
attempt is retained as `positioning_fast_composite_evaluation_pre_alignment`
and is not rewritten. The shared-calendar rule changes the evaluation contract
and definition hashes; its three corrected definitions count as new trials.

## Required evidence

The existing production evaluator remains authoritative. Admission requires a
verified `PASS` with catalog-wide DSR, replayable three-member PBO, computed
leakage evidence, regime profitability, sequential walk-forward persistence,
coverage, redundancy, and immutable dataset/code/search bindings. A timeout,
429, empty result, malformed artifact, unverified record, `FAIL`, or
`REQUIRES_HUMAN_REVIEW` is not a pass.

Even a historical `PASS` allows only the independent forward-paper admission
step. It does not itself authorize Testnet intent generation, credential use,
execution-service startup, Mainnet, Micro-Live, real capital, or order
submission.
