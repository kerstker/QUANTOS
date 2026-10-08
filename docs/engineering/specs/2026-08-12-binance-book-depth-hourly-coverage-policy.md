# Binance book-depth hourly coverage policy

Status: **FROZEN BEFORE CONFIRMATION OUTCOME**  
Date: 2026-08-12

## Decision

The governed `binance_usdm_book_depth_curve_hourly_v2` archive requires all of
the following before a UTC day can enter research:

- the official Binance Data Vision ZIP and checksum match;
- at least 2,592 of the nominal 2,880 daily snapshots are present (90%);
- all 24 UTC hours are present;
- each hour retains at least 96 valid snapshots, exactly 80% of the nominal 120;
- inverted snapshots remain within a 0.2% bounded quarantine, equal to 2% of
  the p10 estimator's tail mass (at least 99.8% structurally valid);
- every hour aligns within the existing 1% bound to all 60 checksum-verified
  Binance USD-M one-minute mark closes for that hour.

No Alpha return from the confirmation window had been computed when this
policy was frozen. The triggering quality failure was
`ADAUSDT / 2026-02-23`: its official checksum passed, the archive contained
2,744 snapshots (95.28% daily coverage), all 24 hours were present, there were
no inverted snapshots, and the only hour below the former ad-hoc 100-snapshot
floor retained 98 observations. The 11 previously admitted days contained
3,696 hourly cross-section rows; their minimum was 101 and their first
percentile was 102.

The former value 100 had no structural interpretation. The replacement 96 is
the predeclared 80% fraction of the nominal two-per-minute sampling schedule.
It admits the observed 98-snapshot hour without changing the 90% day gate,
24-hour continuity, checksum, quarantine, or external price-alignment gates.
An hour with 95 or fewer valid snapshots remains blocked. This is a data
quality policy decision only; it does not grant forward credit, formal Alpha
admission, Testnet authority, credential access, broker connectivity, or order
submission.

The next resumed quality-only pass (still before any confirmation return was
computed) found `DOTUSDT / 2026-03-03` with four crossed snapshots among 2,791
(0.143%), distributed across three hours. All four crossings occurred only at
the nearest 0.2% curve level; the archive checksum passed, all hours remained,
daily coverage was 96.91%, and the least-populated valid hour retained 104
snapshots. Across the 1,526 previously admitted instrument-days, 96 contained
any quarantine, 100 snapshots were quarantined in total, and the former maximum
was two per day (0.0752%). The old 0.1% cap was an uncalibrated round number.
The new 0.2% cap has a direct robust-statistics interpretation: no more than 2%
of the observations feeding a p10 tail estimate may be removed. Four of 2,791
passes; six of 2,791 fails. All other quality gates remain unchanged.

The next quality-only pass found `ADAUSDT / 2026-03-26` with 2,590 official
checksum-verified snapshots, two below the unchanged 90% daily floor of 2,592.
All 24 hours and all 24 mark references existed, the minimum hour retained 101
valid snapshots, and no inversion was present. The daily floor is deliberately
not lowered to fit this file. Instead, the separately frozen confirmation
quality policy excludes the entire UTC day across all 14 instruments whenever
any one instrument fails an existing parser or alignment quality gate. At most
four such days may be excluded: four isolated gaps remove at most eight of the
89 possible 24-hour decisions, leaving at least 81 versus the pre-registered
minimum 80. A fifth quality-rejected day blocks the study. Network failures,
timeouts, checksum mismatches and unexpected errors are never excludable.
