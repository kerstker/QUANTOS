# Cross-venue borrow short-pressure forward experiment

Status: implementation complete locally; not yet frozen on DGX and not an
Alpha result.

## Mechanism before formula

The hypothesized payer is a leveraged short crowd that pays either the cost of
borrowing the asset or negative perpetual funding. Venue-fragmented financing
and inventory constraints can prevent this pressure from clearing instantly.
The primary claim is deliberately narrower than either academic paper used as
provenance: when public OKX asset-borrow cost is cross-sectionally high and
rising, and Binance funding plus mark/index basis are both negative after a
24-hour decline, a crowded short unwind may fund a 24-hour long-perpetual
rebound after executable costs.

The equity lending papers establish that lending fees can reflect short demand,
lending supply, disagreement, and information. They do not validate a crypto
direction, horizon, threshold, or execution rule. Those items are frozen in the
forward contract rather than imported from the papers.

## Data and PIT boundary

- OKX authority: keyless public borrow-rate snapshot plus
  `okx_borrow_vintages.db`.
- Binance authority: keyless public USD-M realtime stream and its PIT feature
  projection.
- Universe: the fixed 14-currency intersection already collected by both
  surfaces.
- All OKX knowledge times and Binance receive/process times must be strictly
  after preregistration and no later than the decision.
- Fetched OKX history is explicitly not PIT before its actual fetch time.
- Deprecated empty amount fields mean borrow availability is unavailable. The
  system may not infer short capacity or historical borrowability from rate.
  The primary trades only a simulated long perpetual; capacity is bounded by
  its executable book, not by fictional borrow quantity.

## Frozen family

The family contains exactly five definitions registered together:

1. primary long rebound after joint borrow/funding/basis/price pressure;
2. the same episodes with the opposite sign;
3. borrow-only ablation;
4. funding/basis-only ablation;
5. cheap-borrow scarcity control.

Selection runs at 02:15 UTC after at least 30 formal forward daily vintages.
The fixed filters, 24-hour label, seven-day per-currency lockout, public quote
latency, fee/spread/depth/partial-fill costs, and double-cost pressure are
content-addressed in the preregistration contract. The pure selection core in
`services/research/borrow_pressure.py` computes only contemporaneous episode
eligibility; it cannot read returns after the decision.

## Unblinding and admission

There is one terminal unblind no earlier than 120 days and only after at least
75 eligible days, 100 non-overlapping instrument decisions, and four decisions
per represented instrument. Before that point only data health and counts are
allowed; no return, Sharpe, candidate comparison, or result-based extension is
allowed.

The final gate is executable incremental value, not single-factor Sharpe. The
primary must produce positive net incremental return versus the best eligible
L2 paper book, survive doubled costs, beat every frozen control, meet DSR/PBO,
drawdown, tail/concentration, correlation, execution-confidence, and USD 1M
perpetual-book capacity requirements. Missing fee, depth, PIT, or control
evidence blocks PASS.

Even a terminal PASS grants only human review for L2 paper admission. It grants
no lifecycle promotion, credential, broker, testnet, order, or live authority.

