# Forward major-universe scope (L2)

## Decision

The forward paper clock uses the fixed `FORWARD_MAJOR_TICKERS` set in
`tools/binance_oi_metrics_fetch.py` (14 instruments). It follows the existing
`crowd_contra` major-book shape; the historical model card's MATIC slot is
replaced by current BCH because MATIC is no longer in the active cache.

`DEFAULT_TICKERS` remains the wider liquid-history universe for historical
research and survivorship diagnostics. The small-cap tail is not a forward
optimization target and cannot block the major-only paper clock.

## Load-bearing consumers

The fixed set is used by the forward data refresh, raw-lineage backfill, data
quality admission, and `tools/forward_track_advance.py` signal panel. Every
instrument in the set must still pass the normal mark-price, funding, OI, and
lineage checks; narrowing the universe does not weaken any quality gate.

The forward panel is built with `build_panel(only=...)`, so excluded history
cannot enter new paper decisions. Existing legacy decisions and wider
historical artifacts are retained as audit evidence and are not rewritten.

When an immutable legacy decision occupies the most recent fixed weekly
anchor, the driver may bootstrap the new attested clock from the latest
available daily event after that ledger timestamp. This is still an admitted
historical data timestamp (never wall-clock or future data); the decision and
price evidence must pass the normal freshness/coverage gates. Realization stays
on the weekly grid with the normal minimum holding gap, so this bootstrap only
removes an administrative wait and does not accelerate the evidence clock.

This is an L2 research/paper scope decision. It does not create broker access,
order submission, or live authorization.
