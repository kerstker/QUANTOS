# RPI Alpha discovery and DGX evidence

Date: 2026-08-24  
Scope: L2 research only; no deployment, credentials, broker connection, order
submission, or live-trading permission.

## Decision

QUANTOS does not currently have a production-eligible Alpha.  The new Binance
RPI-inclusive deep-book surface is a genuinely independent public observation
surface, but the pre-registered one-hour v1 family is not a usable net Alpha on
the evidence collected so far.  The family remains exploratory until 30 common
days and must not be tuned or promoted from the result below.

## What "usable Alpha" means

A candidate is useful only when all of the following are separately evidenced:

1. **Independent, PIT-safe data**: source integrity, fixed universe, known
   knowledge time, freshness, and no future observations.
2. **Economic mechanism**: a written hypothesis and a written falsification
   standard exist before outcome access.
3. **Complete trial accounting**: every candidate/configuration is registered;
   no winning-only ledger and no family reset.
4. **Statistical stability**: locked holdout, DSR, family PBO, walk-forward,
   regime breadth, and drawdown gates all pass.
5. **Execution reality**: latency, turnover, spread/slippage/fees, doubled-cost
   pressure, capacity, and crowding are measured rather than assumed.
6. **Portfolio increment**: the candidate improves the currently deployable
   book after costs and common-risk decomposition; standalone Sharpe is not
   sufficient.
7. **Tail and operational survival**: scenario/tail behavior, degradation,
   kill-switch, reconciliation, and repeatable forward operation are sound.
8. **Governed promotion**: independent challenge and physically separated
   blinded Shadow evidence mature before any human promotion decision.  An AI
   worker cannot promote or trade the candidate.

## Implemented slice

The v1 family contains exactly three one-hour trials:

- `rt_rpi_depth_continuation_1h_v1`
- `rt_rpi_depth_reversal_1h_v1`
- `rt_rpi_top_divergence_1h_v1`

The implementation adds:

- an optional, domain-checked RPI field to the existing realtime frame;
- PIT enrichment using only `knowledge_time <= decision_time` and a three-hour
  maximum age;
- an exact 14-symbol fixed-universe admission gate and authority-chain check;
- fixed economic hypotheses and falsification standards;
- three-day exploration and thirty-day formal-history boundaries;
- global trial-ledger registration before ranking;
- DSR, same-horizon family PBO, locked holdout, walk-forward, spread-regime,
  doubled-cost, and drawdown gates;
- a hard separation between `statistical_validation_gate_pass` and deployment.
  Capacity, portfolio increment, independent challenge, and Signal Shadow are
  explicit blockers, so the durable evaluation remains
  `REQUIRES_HUMAN_REVIEW` even if historical statistics later pass.

Repeated evaluation of the same fixed definitions does not double-count the
three trials.

## DGX isolated diagnostic

The code was run on DGX against the live public-data authorities while using an
isolated temporary experiment database and output path.  Production data was
read-only; the immutable release, production experiment registry, services,
timers, and safety flags were unchanged.

- RPI authority chain: valid
- fixed universe: 14/14 exact symbols
- rows: 6,987
- common span: 2,247,131,996 ms = 26.01 days
- newest feature age at run: 16,323 ms
- evaluated periods per candidate: 444
- pre-registered/evaluated/statistical-pass/promotion-eligible: `3/3/0/0`
- dataset version:
  `7ac92958a17ec4912e2b388c00efdde85aa72d561f4c36e0004cdce2ba7c2b22`
- raw isolated report SHA-256:
  `6af4586b3cbd62c4674aa7cedf3b3ea69ebab848135566ce6b45e6fde0f9ac8a`

| Candidate | mean IC | IC t | Gross Sharpe | Net return | Net Sharpe | Holdout net Sharpe | Turnover | MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| continuation | -0.0233 | -1.755 | -6.708 | -23.85% | -22.071 | -9.531 | 0.638 | 25.01% |
| reversal | +0.0233 | +1.755 | +6.708 | -10.27% | -8.691 | -13.484 | 0.638 | 12.30% |
| deep/top divergence | -0.0063 | -0.461 | -2.463 | -35.56% | -30.043 | -27.083 | 1.369 | 35.73% |

The reversal direction contains weak gross directional information, but one-hour
turnover and the fixed 6 bps cost model consume it.  Its 2x-cost net return is
-25.72% and 2x-cost Sharpe is -23.74.  The result is therefore a falsification of
the current executable one-hour implementation, not evidence for promotion.
PBO=0 here only describes relative family ranking and cannot rescue an
economically losing family.

The isolated diagnostic registry contained only these three trials, so its DSR
values are not a substitute for the production global-trial context.  This does
not affect the conclusion: all three candidates already fail net economics,
holdout, walk-forward, regime, and pressure gates.

## Next controlled development

1. Keep v1 frozen and collect through the 30-day boundary; do not lower gates or
   re-label the current result.
2. Before looking at additional outcomes, pre-register a separate
   execution-feasibility family for lower-frequency persistence/decay.  Every
   horizon is a trial, the existing 26-day window is exploratory/training only,
   and formal credit starts on a future locked window after registration.
3. Replace repeated full-window scans with an append-only hourly RPI/price panel
   or indexed incremental cache before scheduling.  The isolated DGX scan took
   about five minutes against the 12 GB feature authority, so this worker is not
   suitable for the current hourly production cadence as implemented.
4. Only a net-positive locked candidate advances to capacity, crowding, common
   risk, and current-book incremental tests, followed by independent challenge
   and physically separated Signal Shadow.  Nothing in this slice authorizes
   Testnet or Mainnet execution.
