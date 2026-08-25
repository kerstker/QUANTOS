# Mechanism-first Alpha development and executable-value objective

Date: 2026-08-25  
Scope: L2 research and dry-run only  
Status: first slice deployed; second-venue forward authority implemented

## Decision

QUANTOS will no longer treat formula novelty or standalone backtest Sharpe as
the research objective. A method may enter the experiment queue only after it
names:

1. the participant expected to pay the edge;
2. the causal mechanism and why it can persist;
3. one independently countable prediction unit;
4. the orthogonal data surface needed to observe the mechanism;
5. locked falsification and leakage controls.

This does not promise an institutional-grade Alpha. It makes the absence of
such evidence explicit and moves effort away from repeated mining of the same
public-price surface.

## Economic-payer contract

`services/research/alpha_methodology.py::AlphaMethodSpec` now requires
`payer_ids`, `persistence_assumption`, and `independent_bet_unit`. The supported
payer classes are forced liquidators, urgent large orders, leveraged crowds,
hedging demand, capital-constrained cross-venue participants, passive
rule-driven flows, slow information reactors, and speculative lottery demand.

A payerless method fails registry validation before any search or return
evaluation. Existing falsifications remain falsifications; naming a new payer
does not reopen their frozen parameter spaces.

## Executable-value objective

The research-resource ranking objective is:

```text
reference AUM
* min(base incremental net return, double-cost incremental net return)
* orthogonality
* execution confidence
* capacity multiplier
/ turnover penalty
/ tail-loss penalty
```

`services/alpha/executable_value.py` implements the pure, deterministic,
fail-closed policy. Capacity is capped at the reference AUM for scoring, so a
large uncalibrated capacity estimate cannot inflate the score without bound.
Only `PASS` rows receive ranks. `FAIL` and `INCOMPLETE` rows remain visible but
unranked.

Every input requires a SHA-256 evidence reference. Missing execution,
capacity, turnover, tail-risk, independent-bet, or pressure-cost evidence is
`INCOMPLETE`; it is never imputed as zero. The adapter in
`services/research/portfolio_evidence.py` can derive only conservative
orthogonality and capacity from the legacy portfolio row. It deliberately does
not reinterpret simulated TCA as realized execution confidence.

This objective allocates research attention only. It is not wired into Alpha
lifecycle promotion, deployment, mandates, broker adapters, or order
submission.

## Orthogonal data-surface audit

`services/data/research_surfaces.py` separates channel existence from
mechanism observability. It audits capabilities rather than file names and
returns one of `MISSING`, `PARTIAL`, `FORWARD_ONLY`, or `HISTORICAL_READY`.

The initial surfaces are:

- multi-venue trades and historical L2;
- order flow, withdrawal/cancellation, and book recovery;
- OI, concentration, taker flow, and liquidation events;
- funding and basis curves;
- borrow, delistings, and historical constituents;
- fees, rebates, rate limits, and lot-size rules;
- on-chain exchange flows and stablecoin liquidity;
- macro vintages, ETF flows, options volatility, and cross-asset regimes.

Snapshot or recent-forward data never becomes historical evidence merely
because a local artifact exists. A surface with all required observations but
without historical depth is `FORWARD_ONLY`.

## First development slice

The first slice is the order-flow and cross-venue data foundation, not another
formula search:

1. Binance public WebSocket snapshots now retain exchange event time, exact
   local receive time, and local processing/flush time independently at both
   component and snapshot level.
2. Aggregate trade and liquidation windows retain first/last event and receive
   clocks. The append-only store requires the three-clock contract for new
   Binance WebSocket rows.
3. Current capability remains honestly `PARTIAL`: Binance is only one venue,
   and its present public stream does not provide an exact order-level
   cancellation cause or a calibrated book-recovery series.
4. The next data acquisition unit is a second venue's public trades plus L2
   with the same three clocks, followed by forward withdrawal/recovery
   materialization. No order-flow Alpha is reopened before that surface and its
   blind forward window mature.

## Second development slice: Bybit public linear events

The second venue is Bybit linear because its USDT perpetual symbols overlap the
existing Binance USDM pilot and therefore make venue-lead, urgent-flow and
capital-constraint questions directly observable. The collector uses only the
official keyless `wss://stream.bybit.com/v5/public/linear` endpoint and fixed
public topics:

- `orderbook.200.{symbol}` snapshots and deltas;
- `publicTrade.{symbol}` source trade batches;
- `allLiquidation.{symbol}` complete liquidation messages;
- `tickers.{symbol}` mark/index/OI/funding state.

The initial pilot is BTCUSDT, ETHUSDT and SOLUSDT. Level 200 is deliberately
chosen over level 50: the official documented cadence is 100ms rather than
20ms, reducing forward storage pressure while retaining meaningful depth.
Every source message is persisted with its source event clock, local receive
clock and local processing clock in `PublicMarketEventStore`; the store is
batch-atomic, append-only, idempotent for exact replays and hash chained.

Orderbook snapshots reset local state. Deltas apply insert/update/delete rules;
delta-before-snapshot, non-increasing sequence, crossed/empty reconstruction,
future-dated source clocks and unallowlisted topics fail closed. Public trade
batches retain every source trade ID, taker side, price, quantity, block flag,
RPI flag and cross sequence. Liquidations retain the liquidated position side,
bankruptcy price and quantity. No SDK, private topic, credential, account or
order surface is present.

The capability registry may therefore mark multi-venue trade/L2 observability
as `FORWARD_ONLY` once the artifact exists. It still does not grant historical
readiness. A zero-size L2 update means a level was filled or cancelled; it does
not reveal the exact cause. Consequently exact cancel rate remains missing and
must not be inferred from deletion count alone. The next materialization is a
PIT book-recovery/venue-lead panel after enough blind forward events accrue;
only then may a new mechanism hypothesis be pre-registered.

The existing liquidation cascade/exhaustion definitions and prior order-flow
definitions remain frozen failures. The ICT sweep contract remains a separate
pre-registered forward observation and cannot be tuned from interim returns.

## Alpha portfolio policy

The unit of breadth is an independent prediction, not an expression count.
Future candidate evidence must record non-overlapping events or holding periods
and an evidence-bound independent-bet count. Portfolio value is measured
incrementally against the incumbent book after factor/common-exposure removal,
cost stress, capacity, turnover, and tail risk.

Many weak mechanisms may ultimately be combined, but a shared data source,
shared payer, or overlapping label horizon does not create independent breadth.

## Real-execution boundary

`L2_DRY_RUN_ONLY` remains correct. Tiny real-capital calibration is permitted
only after a sufficiently long blinded forward record and a separate human
mandate. It is intended to estimate queue position, partial fills, rejects,
market movement, and adverse selection—not to rescue a failed research result.

The following remain false and are not changed by this work:

- `LIVE_TRADING_ENABLED`
- `REAL_BROKER_ENABLED`
- `ALLOW_ORDER_SUBMISSION`
- `ALLOW_AI_DIRECT_TRADING`

`REQUIRE_HUMAN_APPROVAL` remains true.

## Acceptance

- payerless methods fail before search;
- missing executable evidence is `INCOMPLETE` and unranked;
- stressed incremental profit, not standalone Sharpe, drives the score;
- recent/snapshot data never masquerades as history;
- exact event/receive/process clocks survive stream reduction and persistence;
- legacy lifecycle and trading safety gates are unchanged;
- local canonical validation and ARM64 DGX tests must pass before deployment.
