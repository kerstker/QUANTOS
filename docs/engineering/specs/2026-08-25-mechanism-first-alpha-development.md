# Mechanism-first Alpha development and executable-value objective

Date: 2026-08-25  
Scope: L2 research and dry-run only  
Status: second-venue authority deployed; first mechanism preregistration implemented

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

## Third development slice: forced-liquidation recovery contract

The first experiment consuming both venue authorities is not a formula sweep.
`tools/cross_venue_liquidation_preregistration.py` freezes one economic
mechanism: forcibly liquidated Bybit accounts can cross a temporarily depleted
book faster than fragmented Binance/Bybit capital can restore convergence.
The independent prediction unit is one instrument-side liquidation burst plus
a 30-minute instrument lockout.

The primary requires all of the following using only data processed before the
decision: a same-side liquidation burst, same-side taker confirmation,
liquidation notional material relative to the pre-event 20bps book, causal book
recovery, and at least a 2bps Bybit pressure overshoot relative to synchronous
Binance quotes. Entry occurs only after a two-second quiet period plus 250ms
latency; the holding horizon is fixed at 60 seconds. Buy-position liquidation
maps to forced sell pressure and Sell-position liquidation maps to forced buy
pressure, matching the source field's explicit liquidated-position semantics.

Exactly five candidates are frozen together:

1. cross-venue liquidation reversion (primary);
2. the continuation sign control;
3. reversion without the Binance-resilience condition;
4. a matched urgent-taker-flow event with no nearby liquidation;
5. a six-hour-shifted clock placebo with no liquidation.

No threshold, sign, horizon, venue filter, or control may change after outcome
access. Unblinding requires at least 90 days, 60 eligible event days, 500
non-overlapping episodes, and 75 episodes per instrument. Interim monitoring is
limited to clocks, chain integrity, coverage, event-day counts, and missing
data; returns, Sharpe, labels, or candidate comparisons are forbidden.

The terminal decision is not Sharpe-only. It requires full evaluator evidence,
`executable_value.v1` PASS, positive incremental net return against the best
eligible existing L2 paper book, double-cost survival, correlation no greater
than 0.60, execution confidence of at least 0.80, at least USD 1 million of
capacity, tail/crowding/regime evidence, and superiority to all four controls.
A missing versioned fee schedule blocks PASS rather than becoming zero cost.
Even a terminal PASS grants only human-reviewed L2 paper admission.

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

## Fourth development slice: outcome-blind maturity authority

`services/research/liquidation_mechanism.py` and
`tools/cross_venue_liquidation_health.py` close the operational gap between a
frozen preregistration and a future terminal evaluator. The pure mechanism
core consumes only liquidation identity, instrument, liquidated-position side,
source event time, local receive time and local processing time. It deliberately
does not expose price, quantity, returns, labels, candidate identity or any
outcome statistic.

Prints are grouped by instrument and side while consecutive receive clocks are
no more than five seconds apart. A burst is counted only after a quiet period
has closed it at a fixed observation boundary. The independent unit then uses
the preregistered 30-minute instrument lockout regardless of side. This makes
the maturity counter deterministic, restart-safe and resistant to inflating
breadth with many prints from one liquidation cascade.

The worker verifies the frozen preregistration intrinsically and by exact
artifact SHA-256, then verifies the Bybit authority from a trusted local
checkpoint through the current suffix. It publishes only collection elapsed
time, raw print count, closed-burst count, independent-episode count, eligible
UTC event days and per-instrument counts. Price and quantity mutations cannot
change maturity. The output contract always keeps interim outcome access,
labels, returns, trial-registry mutation and Alpha eligibility disabled.

The worker runs every 30 minutes at minute 17/47 with a ten-minute bound and a
two-hour watchdog freshness contract. Its status artifact and the immutable
preregistration artifact are included as optional sources in encrypted backup
contract v9. The first DGX v9 backup included 23 real files and passed source
contract, encryption, separate-filesystem, manifest, SQLite integrity, restore,
restore-history, RPO and RTO checks.

## DGX release evidence and public-book resilience

The canonical gate for source commit `1e86dd7c` completed 27/27 checks: 6,685
core tests passed with 19 skipped, 2,886 live/trading/tools tests passed with two
skipped, 199 frontend tests passed, and all 24 Phase 13 audit tests passed. The
evidence-bound ARM64 release is
`fd5f31c4d5c53587aec3687c6a95a8c0cc655861`; 305 focused ARM64 tests passed on
the DGX.

Production observation exposed repeated TLS EOFs on the public Bybit REST path
used by the paper multi-venue SOR. The HTTP boundary now retries only transient
URL/timeout transport failures with fixed 0.25-second and 0.75-second delays.
Explicit HTTP responses and malformed payloads remain immediate failures, and
the third transport failure still blocks the whole canary. No alternate domain,
stale-cache substitution, private endpoint or execution capability was added.
After deployment, one manual and one independently scheduled 14-instrument
canary both passed with four approved simulation intents, zero rejects, no
Binance REST failover and every credential/broker/order/live flag false.

All 39 non-Attention workers and four special unit files now bind the new
release and passed `systemd-analyze verify`. The separately frozen Wikimedia
Attention service and timer retained their exact hashes and older release.
Binance and Bybit resident streams restarted into the new release and returned
safe semantic PASS receipts. A deployment-time overlap between the manual SOR
and domain-event projection produced one retained SQLite OperationalError; the
normal idempotent recovery worker cleared the single pending event with all
three append-only chains valid and zero dead letters.

The final watchdog snapshot had zero breaches, 34 passes, nine partial results
and three operator-owned unconfigured external authorities. The blind sample
at that snapshot had 394 forward liquidation prints, 122 closed bursts and only
12 independent episodes across one UTC event day (four each for BTC, ETH and
SOL). Every maturity gate remains false, unblinding remains prohibited, formal
Alpha eligibility remains zero, and the platform remains `L2_DRY_RUN_ONLY` and
`NOT_TOP_TIER` (14/22).
