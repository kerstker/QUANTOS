# ICT liquidity-sweep forward experiment v1

Date frozen: 2026-08-25  
Readiness: `L2_DRY_RUN_ONLY`  
Authority: append-once forward research protocol only

## Evidence boundary

The Inner Circle Trader education library is a practitioner-primary source,
not peer-reviewed profitability evidence. Its liquidity-sweep and session
language is translated into observable events and never treated as proof of
institutional intent. Cont, Kukanov, and Stoikov, *The Price Impact of Order
Book Events*, DOI `10.1093/jjfinec/nbt003`, supplies a peer-reviewed mechanism
for order-flow imbalance and price impact, not validation of the ICT trading
rule. The combined claim must therefore earn its own forward evidence.

No retained outcome was inspected to choose this translation. Existing
realtime outcome reports and historical labels are outside the experiment.
Only frames with decision time strictly after the DGX append-once
preregistration timestamp can ever be admitted.

## Frozen observable translation

- Universe: ADA, ATOM, AVAX, BCH, BNB, BTC, DOGE, DOT, ETH, LINK, LTC, SOL,
  UNI, and XRP Binance USDM perpetuals.
- Input: causal 30-second PIT mid, spread, five-second taker imbalance,
  five-level displayed-depth imbalance, and five-level executable USD depth.
- Prior level: high/low of exactly 120 contiguous frames (60 minutes), all
  strictly before the breach frame.
- Breach: mid moves beyond the prior high/low by at least one contemporaneous
  full spread. Same-side taker imbalance must reach absolute 0.50 for the
  absorption-qualified candidates.
- Reclaim: first mid back inside the prior level within four frames (two
  minutes). Displayed absorption requires opposite depth imbalance of at least
  0.25 and executable depth no lower than at the breach. This is explicitly a
  displayed-depth proxy, not knowledge of hidden intent.
- Decision: first qualifying reclaim frame plus 250 ms. Exit: first complete
  frame at or after 15 minutes. Missing frames are never filled; the affected
  formation or label is ineligible.
- Non-overlap: one event per instrument per 15-minute holding horizon.
- Sessions: DST-safe `America/New_York` local hours 02:00–05:00 and
  07:00–10:00. The equal-duration clock placebo is 14:00–17:00 and
  19:00–22:00. Session windows cannot be changed after outcomes.

Exactly five candidates are frozen:

1. session absorption reversal primary;
2. all-day absorption reversal control;
3. session sweep-only reversal control, without flow/depth qualification;
4. session absorption continuation sign control;
5. absorption reversal in the clock-placebo sessions.

There is no level, flow, depth, reclaim, holding, session, sign, universe,
cost, or threshold scan.

## Portfolio, cost, and capacity

Eligible event returns are equal-risk aggregated within each UTC day to form
one daily candidate return, preventing millions of correlated 30-second rows
from masquerading as independent observations. Gross exposure and leverage
are capped at 1.0. Diagnostic target AUM is USD 100,000, maximum participation
is 5%, and capacity is calibrated from PIT executable depth.

Base costs include 4 bps fee per side, 2 bps latency slippage per side,
contemporaneous half-spread crossing at entry and exit, and calibrated impact.
The pressure replay doubles fees, latency slippage, and the impact coefficient.
Missing impact/capacity evidence fails closed.

## Unblinding and decision bars

Return statistics are forbidden until all of these data-only conditions hold:

- at least 90 elapsed days after preregistration;
- at least 60 eligible UTC event days;
- at least 500 non-overlapping eligible events.

Before then, only input freshness, coverage, gap, event-count, instrument-count,
and hash diagnostics may be inspected. Incompleteness can extend collection;
outcomes can never shorten or extend the window. The terminal evaluator must
register all five candidates in the global trial population before accessing
any return.

The primary passes only if every frozen bar holds: evaluator/full-evidence
PASS; net Sharpe at least 1.0 and positive annual return; DSR at least 0.95;
PBO at most 0.25; maximum drawdown at most 10%; locked final-third Sharpe at
least 0.80; 75% positive walk-forward fraction; positive double-cost return and
double-cost Sharpe at least 0.75; at least +0.20 Sharpe over both all-day and
sweep-only controls; beats continuation and placebo-session controls; at least
80% positive leave-one-instrument-out fraction; no instrument exceeds 20% of
events; no day contributes more than 10% of total PnL; participation no greater
than 5%; and complete capacity, crowding, regime, and tail evidence.

A terminal historical/forward pass can only justify human review for L2 paper
admission. It grants no Shadow, Testnet, credential, broker, order, or live
authority.

## Implementation boundary

`services/research/ict_liquidity_sweep.py` is the deterministic pure event and
signal kernel. `tools/ict_liquidity_sweep_preregistration.py` is an append-once
contract recorder that reads no prices, feature values, labels, returns,
registry, lifecycle, credentials, broker, or order surface. The DGX raw feature
collector continues independently; recording this protocol does not evaluate
or trade the hypothesis.

## DGX preregistration evidence

The complete immutable ARM64 release is
`c7fb7d8e34ff35f93560149c675534c426f9ae07`. The DGX focused suite passed 38
tests covering the pure kernel, append-once recorder, methodology catalog, and
crypto-data governance boundary. Immediately before recording, the realtime
feature authority contained 30,300,306 rows. Its maximum knowledge time was
`1787637658895`, 83.204 seconds earlier than the freeze, so no retained frame is
eligible for this experiment.

DGX recorded the contract exactly once at `1787637742099`:

- artifact:
  `/srv/quantos/state/evidence/ict_liquidity_sweep_preregistration_c7fb7d8.json`;
- preregistration ID:
  `a735078a00fbc0510087db37394089f09b4d905be193a94d1d3cf999f60bbece6`;
- contract hash:
  `b4679084d69962ee3361139c33290ab97d217b38a7ea64e443d363356306e423e`;
- artifact SHA-256:
  `2e2fbb946e4b2f0d9e6969f09962db96c0ed7cefc8197068049279d5de07877d`;
- earliest time-based unblind boundary: `1795413742099`, still subject to the
  independent 60-day and 500-event minimums.

The artifact is `root:quantos` mode `0440`. A deliberate second recording was
rejected with `FileExistsError` and left the digest unchanged. The global trial
count remained 2,860; `outcomes_accessed=false`,
`return_statistics_computed=false`, `trial_registry_mutated=false`, and
`alpha_eligible_count=0`. This is evidence that the experiment is frozen and
collecting, not evidence that the Alpha works.
