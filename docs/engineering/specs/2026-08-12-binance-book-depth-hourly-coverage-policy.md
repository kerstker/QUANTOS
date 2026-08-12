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
- inverted snapshots remain within the existing 0.1% bounded quarantine;
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
