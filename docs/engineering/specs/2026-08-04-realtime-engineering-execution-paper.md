# Realtime Engineering Execution Paper

Status: IMPLEMENTING  
Scope: L2 public-data paper only  
Gate impact: none; this evidence cannot satisfy formal forward, promotion, deployment, or live gates.

## Problem

The autonomous realtime pipeline currently freezes decisions before outcomes and
later records signal realizations.  That is a valid Signal Shadow, but it does
not prove the production execution seam: intent, pre-trade permission, venue
routing, simulated fills, positions, fees, PnL, TCA, reconciliation, restart,
and idempotent replay.

The existing simulated SOR also represents quantity as an integer base-asset
unit.  A small paper account therefore rounds BTC/ETH targets to zero.  This is
not an honest production-paper approximation for crypto contracts whose venue
quantity step is fractional.

## Decision

Add a physically separate `AUTONOMOUS_REALTIME_ENGINEERING_EXECUTION_PAPER`
ledger.  Every evaluated realtime candidate is admitted immediately, but the
first observed Signal-Shadow decision is only a baseline: execution begins on
the next decision created after admission, so startup never backfills a stale
decision against a current book.

For each new decision the worker:

1. verifies the Signal-Shadow hash chain and immutable candidate admission;
2. verifies fresh Binance contract filters and the hash-chained two-venue book;
3. converts the conservative whole-base-unit book into integer exchange-step
   lots (`lot = venue step_size`, `lot_price = native_price * step_size`);
4. adapts the frozen realtime weights into the existing paper preview seam;
5. runs the normal pre-trade gate, deterministic simulated SOR, dry-run adapter,
   fills, position/PnL reconciliation, and route TCA;
6. appends a complete fill preview, or an explicit `NO_ACTION` resolution when
   the fully reconciled preview contains zero intents, to independent
   hash-chained SQLite ledgers;
7. exposes exact lot/native quantity metadata, latency, costs, residual, and
   safety fields through the console SSE projection.

The conversion is conservative: persisted depth already aggregates fractional
venue depth into completed base units at the worst contributing price.  Splitting
that proven whole-unit depth into exchange-step lots cannot invent liquidity;
it only restores the venue's executable quantity granularity.

## Fail-closed rules

- no execution of the admission-baseline decision;
- up to 64 unresolved post-admission decisions per track are resolved in
  timestamp order per cycle; additional decisions remain visibly queued for
  the next cycle;
- decisions older than the 12-minute execution window are appended as
  `MISSED_EXECUTION_WINDOW` in an independent append-only hash chain and are
  never silently dropped or falsely backfilled against a current book;
- a legitimate zero-intent decision is appended as `NO_ACTION`; it resolves the
  decision idempotently but is not counted as an execution, order, fill, TCA,
  or missed execution window;
- stale/missing contract filters, book bundle, source ledger, or reconciliation
  block the candidate cycle;
- a reused `(candidate_id, decision_time_ms)` with changed content is rejected;
- no credentials, private stream, broker connectivity, venue SDK, or order API;
- official Paper remains governed solely by `ALPHA_PRODUCTION_WORKFLOW`;
- engineering rows have `promotion_eligible=false`,
  `forward_evidence_eligible=false`, `deployment_eligible=false`,
  `live_allowed=false`, and `order_submission_allowed=false`.

## Acceptance evidence

- all 22 currently admitted candidates appear in the execution authority;
- fast-horizon candidates automatically produce routed fills after their next
  Signal-Shadow decision;
- every successful row has route TCA, zero unexplained reconciliation residual,
  exchange-step quantity metadata, and a valid append-only chain;
- exact retry produces no duplicate side effect;
- restart preserves positions and continues from the next decision;
- console updates over SSE without page refresh;
- formal pass count and live/order flags remain unchanged.
