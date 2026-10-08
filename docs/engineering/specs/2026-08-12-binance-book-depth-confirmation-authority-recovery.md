# Binance book-depth confirmation authority recovery

## Decision

The 60,816-row local book-depth authority recorded by commit `143c417` is no
longer present.  Its committed capacity evidence remains valid evidence of the
prior run, but neither that JSON nor an older encrypted backup may be presented
as the missing SQLite authority.  The old append-only chain is therefore marked
unrecoverable, not silently recreated.

Before any confirmation Alpha outcome is accessed, recovery is frozen as a new
authority chain over the exact same public source window.  The worker must
download all 97 UTC days from Binance Data Vision, verify both book-depth and
mark-price checksums, and apply the already-frozen estimator-aligned v4 quality
policy without threshold changes.

## Exact acceptance

The reconstructed source is admissible only if it independently reproduces all
source-level facts committed before outcome access:

- exactly 91 complete UTC days and 30,576 accepted hourly observations;
- exactly the six previously recorded excluded dates, with no imputation;
- 85 complete parent days and six complete supplement days;
- exactly 84 actual adjacent 24-hour decisions;
- a valid new append-only chain whose knowledge time is the new fetch time.

Any mismatch blocks before signal, return, IC, PnL or confirmation metrics are
computed.  Matching source facts prove deterministic reconstruction; they do
not make the new chain hash equal to the lost chain, because knowledge time is
truthfully later.

## Safety

The recovery uses public unauthenticated archives only.  It does not read keys,
connect a broker, generate an intent, authorize Testnet, submit an order, grant
forward credit or formally admit an Alpha.  A recovered source still has to
pass the one-shot confirmation and the formal validation DAG.
