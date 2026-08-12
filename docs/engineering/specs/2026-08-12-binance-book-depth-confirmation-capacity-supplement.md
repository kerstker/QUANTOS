# Binance book-depth confirmation capacity supplement

Status: **PRE-REGISTERED BEFORE SUPPLEMENT DATA ACCESS**  
Date: 2026-08-12

The estimator-aligned 90-day confirmation replay produced 85 complete days but
only 79 actual adjacent 24-hour decisions. No candidate signal, return, or
confirmation metric had been accessed. The missing decision is caused by four
days with hourly coverage below the fixed 96-observation floor and one ETH hour
that exceeded the unchanged 1% independent mark-alignment bound. Those hard
gates remain unchanged.

This supplement freezes the immediately preceding seven UTC days,
2026-02-05 through 2026-02-11. The authority contained zero observations for
that interval at freeze time. All seven days must be processed; no date or
period may be selected after collection. The supplement uses the exact same
candidate, direction, 24-hour horizon, universe, 6bps cost, double-cost stress,
and Alpha gates as the parent confirmation. Parent and supplement periods are
evaluated as one chronological sample only if their combined actual adjacent
decision count reaches the original minimum of 80.

This is a capacity repair, not a new trial and not a threshold change. Missing,
checksum-invalid, structurally invalid, hour-incomplete, or mark-misaligned data
fails closed. A passing combined confirmation still grants neither formal
admission, forward credit, Testnet authority, nor order-submission permission.
