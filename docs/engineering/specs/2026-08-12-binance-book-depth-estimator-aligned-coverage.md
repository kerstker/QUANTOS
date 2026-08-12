# Binance book-depth estimator-aligned coverage

Status: **FROZEN BEFORE CONFIRMATION OUTCOME**  
Date: 2026-08-12

The confirmation candidate consumes one p10 depth-curve estimate per UTC hour.
Its source-quality admission rule is therefore defined at the estimator's
actual consumption grain: all 24 hours must exist and each hour must retain at
least 96 structurally valid observations, or 80% of the nominal 120. Official
checksums, exact symmetric levels, the 0.2% bounded inversion quarantine, and
checksum-verified mark-price alignment remain hard requirements.

The former additional daily aggregate floor of 2,592 observations is retired
as an admission gate and retained as a diagnostic. It is not monotone with the
hourly estimator requirement: a checksum-valid day can have fewer than 2,592
total observations while every one of its 24 p10 estimates has more than the
required 96 observations. Rejecting such a day does not protect the stored
estimator and unnecessarily removes two adjacent 24-hour decisions.

This decision follows the quality-only replay committed at `b946cf7`. The replay
loaded no mark prices, signals, or returns. Across the seven previously excluded
days, three days had all 24 hours and minimum hourly counts of 101, 101, and 102;
four days had minimum hourly counts of 90, 89, 92, and 94 and remain excluded.
The policy is general and content-based—there is no date whitelist.

The pre-registered confirmation gates are unchanged: at least 81 complete days,
at least 80 actual adjacent 24-hour decisions, no imputation, and all original
Alpha thresholds. A confirmation pass still grants neither formal admission,
forward credit, Testnet authority, nor order-submission permission.
