# Outcome-free market-resilience forward data

Date: 2026-08-25  
Scope: Bybit public linear WebSocket data, L2 research only.

## Decision

The next mechanism-first increment is a durable one-second market-resilience
surface, not another return search. It records how urgent signed flow and the
displayed book coexist at the end of each collector flush. It does not define a
trade, inspect a future price, compute a label, register a trial or grant Alpha
eligibility.

The existing cross-venue forced-liquidation preregistration already uses
non-liquidation urgent flow as a payer control. That control must not be recycled
as a separate Alpha before the terminal unblind. This data surface is therefore
shared measurement infrastructure only. A later hypothesis requires a separate
preregistration, non-overlapping prediction units and explicit family-level
trial accounting.

## Primary evidence and translation boundary

- Cont, Kukanov and Stoikov, *The Price Impact of Order Book Events*,
  DOI `10.1093/jjfinec/nbt003`: order-book event imbalance and displayed depth
  motivate retaining signed flow and depth jointly.
- Easley, López de Prado and O'Hara, *Flow Toxicity and Liquidity in a
  High-frequency World*, DOI `10.1093/rfs/hhs053`: signed volume imbalance and
  trade intensity motivate retaining observable toxicity inputs, not claiming
  that VPIN or a directional rule is profitable in crypto.
- Roşu, *A Dynamic Model of the Limit Order Book*, DOI
  `10.1093/rfs/hhp011`: temporary impact and overshoot motivate measuring book
  replenishment separately from permanent price movement.
- Hautsch, Scheuch and Voigt, *Building Trust Takes Time: Limits to Arbitrage
  for Blockchain-Based Assets*, DOI `10.2139/ssrn.3302159`: settlement latency
  and venue segmentation motivate preserving venue and three-clock provenance.

These sources are mechanism priors. None validates the future QUANTOS episode
definition, net execution economics or crypto persistence.

## Observation contract

The collector emits at most one derived observation per instrument per flush,
and only when:

1. a complete uncrossed Bybit L2 book exists from a source snapshot followed by
   strictly increasing deltas;
2. at least one order-book or public-trade source event for that instrument was
   admitted in the flush;
3. every source event has
   `event_time_ms <= received_at_ms + 30s <= processed_at_ms + 30s`, while local
   receive time never exceeds local process time;
4. the raw source batch has already committed to the append-only public-event
   authority and its exact head sequence/hash is available.

Each observation binds:

- instrument, venue and a reconnect/book-snapshot segment ID;
- exchange event, local receive and local process clocks;
- Bybit update ID and cross sequence;
- best bid/ask, mid and spread in basis points;
- quote notional inside 5, 10 and 20 bps on both sides;
- 20-bps displayed-depth imbalance;
- buy/sell/signed taker notional, taker imbalance and trade count;
- RPI and block-trade counts;
- book update count and observed level-deletion count;
- the exact source event IDs plus raw authority head sequence/hash;
- source-to-receive and receive-to-process latency.

All decimal quantities use canonical decimal strings. A zero-size book update is
called a `level_delete`; it is never relabelled a cancellation because public
data cannot distinguish fill, cancel and other removal causes. The observation
therefore carries `cancel_rate_inferred=false` and cannot satisfy a capability
requiring exact cancellation rate.

## Persistence and restart semantics

`MarketResilienceStore` is a separate SQLite authority. It is append-only,
batch-atomic, idempotent for exact snapshot replay and hash chained. Reusing a
snapshot ID with different content fails. Update and delete triggers reject
mutation. Each derived row is bound to the already committed raw authority head.

A WebSocket source snapshot creates a new deterministic book-segment ID. The
first observation after restart/reconnect therefore starts a new segment rather
than pretending uninterrupted queue or recovery continuity. The collector does
not backfill derived observations for a disconnected interval.

## Collector boundary

The raw event authority remains primary. A flush commits and verifies raw events
before deriving resilience observations. Derived persistence is deterministic
from the current complete book plus that same flush's admitted source events.
The resident status reports the derived authority head and chain health.

The resilience database is irreplaceable forward evidence after it exists and
must enter the encrypted backup source contract. It remains outcome-free and
does not enter evaluator, lifecycle, paper admission, Testnet or live paths.

## Safety and honesty

- official keyless Bybit public data only;
- no credential, private stream, account, broker SDK or order surface;
- no future price, return, Sharpe, label or candidate comparison;
- no exact cancel-rate claim;
- no queue-position or fill-probability claim;
- no historical credit before the first retained derived observation;
- no Alpha eligibility, promotion, Testnet or live authority.

## Acceptance

- pure calculations reject invalid books, clocks, sides, quantities and source
  identities;
- 5/10/20-bps depth and taker metrics have exact deterministic tests;
- later source events cannot mutate an already produced snapshot;
- reconnect snapshots produce a new segment;
- store replay is idempotent and conflicting reuse/update/delete/tamper fails;
- collector snapshots bind the raw authority head and expose no outcome field;
- governance proves `services/` remains network-free and L2 safety is unchanged;
- canonical local validation and focused ARM64 DGX tests pass before deployment.
