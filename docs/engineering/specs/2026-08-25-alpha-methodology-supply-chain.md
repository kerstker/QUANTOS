# Alpha methodology supply chain

Date: 2026-08-25  
Scope: L2 research and dry-run only. No credential, broker, order-submission,
Testnet, Mainnet, or Alpha-promotion authority is introduced.

## Decision

QUANTOS now treats external trading knowledge as an auditable research input,
not as a ready signal.  A source must be classified, translated into a fixed
economic claim, assigned data requirements and failure modes, and placed in the
global trial accounting process before its outcome can be inspected.

"All papers" is an unbounded and continuously changing universe.  The safe
system interpretation is therefore an extensible, machine-validated registry
with a curated first corpus, not a claim that the literature has been exhausted.

## Evidence classes

1. `PEER_REVIEWED`: an academic prior.  Cross-asset evidence does not become
   crypto evidence without a separate crypto experiment.
2. `WORKING_PAPER`: a citable research prior with lower publication maturity.
3. `PRACTITIONER_PRIMARY`: a claim taken from its originator.  It remains a
   practitioner hypothesis and can never be relabelled academic by the registry.

Profit screenshots, third-party summaries, social-media popularity, and a
positive backtest are not evidence classes.

## First source corpus and system translation

| Method | Primary source | Translation in QUANTOS | Current disposition |
|---|---|---|---|
| Momentum | [Jegadeesh–Titman](https://www.nber.org/papers/w7159), [Liu–Tsyvinski](https://www.nber.org/papers/w24877) | fixed formation/holding windows, sign and crash controls | existing tested family |
| Carry | [Koijen–Moskowitz–Pedersen–Vrugt](https://www.nber.org/papers/w19325) | settled funding cash flow separated from price return | existing tested family |
| Pairs relative value | [Gatev–Goetzmann–Rouwenhorst](https://www.nber.org/papers/w7032) | formation-only pair selection, disjoint locked trade window, random-pair bootstrap | new experiment gap |
| Order-flow imbalance | [Cont–Kukanov–Stoikov](https://academic.oup.com/jfec/article-abstract/12/1/47/816163) | pre-decision aggressive flow × depth with latency/spread pressure | existing implementation failed net economics |
| Crypto price discovery | [Makarov–Schoar](https://www.aeaweb.org/articles?id=10.1257%2Fpandp.20191020) | synchronous event-time lead/lag, never quote-only arbitrage | existing implementation failed |
| Positioning reversal | [Grinblatt–Han](https://www.nber.org/papers/w8734) | public positioning extremes with continuation and venue controls | existing implementation failed |
| Liquidity premium | [Bekaert–Harvey–Lundblad](https://academic.oup.com/rfs/article-abstract/20/6/1783/1575135) | future-ADV-safe liquidity measure plus participation/impact limits | new experiment gap |
| Crypto attention | [Liu–Tsyvinski](https://www.nber.org/papers/w24877) | timestamped attention innovation conditioning fixed momentum | blocked: no governed attention channel |
| ICT liquidity sweep/session | [Huddleston primary library](https://www.theinnercircletrader.com/) | stop-level breach + aggressive flow + replenishment + fixed session; random-session placebo | future pre-registration only |
| ICT fair-value gap | [Huddleston primary library](https://www.theinnercircletrader.com/) | a literal three-bar non-overlap/fill rule; no claim of institutional intent | future pre-registration only |

The ICT entries deliberately separate vocabulary from evidence.  "Liquidity",
"order block", "fair value gap", and "kill zone" are converted into exact
timestamped predicates.  Narrative confluence, discretionary chart annotation,
and post-result rule addition are prohibited.

## Discovery controls are part of the method

- [Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
  maps to the global trial ledger and `services/validation/deflated_sharpe.py`.
- [Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)
  maps to same-calendar family CSCV/PBO.
- Locked OOS, purging/embargo, doubled-cost pressure, regime breadth, tail risk,
  capacity, common-risk decomposition, portfolio increment, degradation, and
  independent Shadow remain mandatory downstream gates.

The development loop is consequently:

`source -> falsifiable hypothesis -> data contract -> pre-registration -> trial ledger -> locked evaluation -> execution reality -> portfolio increment -> forward Shadow`

At no point is the loop allowed to become "try variations until one is green".
Failed trials remain in the population, and a new search round must introduce a
new mechanism or genuinely independent data rather than reset the family count.

## Implemented slice

- `services/research/alpha_methodology.py` is the deterministic registry and
  validator.  It has no filesystem, network, clock, lifecycle, or execution seam.
- `tools/alpha_methodology_audit.py` joins the registry to the local data-channel
  inventory and creates a deterministic research-supply ranking.
- The output purpose is `ALPHA_RESEARCH_SUPPLY_NOT_PROMOTION`; it always reports
  `alpha_eligible_count=0`.
- Practitioner provenance, missing inputs, recent/diagnostic data, leakage risks,
  falsification tests, and existing failed implementations stay visible.

## DGX observation and next experiments

The 2026-08-25 DGX read-only inspection found all 22 declared channel artifacts
except the intentionally missing liquidation archive and ALFRED vintage store.
The most important current assets include a 59.7 GB public realtime stream, a
220 MB RPI authority, 327 MB realtime market state, 270 MB book-depth archive,
and real Binance/Bybit/Hyperliquid/CFTC/CoinMetrics histories.

RPI low-frequency v2 remains frozen: registration was 2026-08-24 21:00 CST and
formal credit starts at 2026-08-25 00:00 CST.  The daily 11:00 timer is active;
no early evaluation or parameter mutation is permitted.

Two non-overlapping next actions follow from the registry:

1. **Academic historical diagnostic:** implement the exact pairs-relative-value
   formation/trade split with a frozen pair budget and random-pair falsifier.
   This is a new trial family, not a rerun of the existing BTC-residual reversal
   cluster.  A historical positive result is diagnostic only and still needs a
   future locked sample.
2. **ICT forward experiment:** pre-register one literal liquidity-sweep/session
   family against the realtime stream.  Its existing history is training only;
   formal credit begins after registration.  FVG remains a separate family so
   failed sweep evidence cannot be rescued by adding confluence.

The 2026-08-24 23:08 `paper_research_risk_bundle` run also detected concurrent
input-authority drift and rejected its worker attestation.  This is a valid
fail-closed result, not Alpha evidence.  It should be retried only in a low-load
window because the DGX had 22 GiB available memory while the external SGLang and
ComfyUI workloads were active.

## Acceptance

- Registry and tool tests pass.
- Duplicate IDs, missing source links, incomplete falsification fields, unknown
  channels, and practitioner-as-academic provenance fail closed.
- Ranking is deterministic under input-map reordering.
- No readiness label contains or implies profitability.
- No method can invoke lifecycle promotion or execution.

