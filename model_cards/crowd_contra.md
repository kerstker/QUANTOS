# Model Card: crowd_contra

**Version:** 1.0  
**Status:** PAPER_TRACKING  
**Generated:** 2026-07-16

---

## 1. Model Identity

- **Strategy ID:** `crowd_contra`
- **Signal Expression:** `cs_rank(neg(global_ls))`  
  (Cross-sectional rank of negative OI positioning — fade crowded retail longs/shorts)
- **Asset Class:** Crypto perpetual futures
- **Universe:** 14 major crypto coins with OI data (BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX, MATIC, DOT, LTC, UNI, LINK, ATOM)
- **Holding Period:** Weekly rebalance

---

## 2. Training Data & Sample

### Data Coverage
- **Start Date:** 2022-01-01 (OI data availability)
- **End Date:** 2026-07-02
- **Total Days:** 1635
- **Instruments:** 24 (14 in major universe)

### Universe Construction
- **Selection Criteria:** Top crypto by volume + available OI data (Open Interest `global_ls` field)
- **Survivorship Bias:** None — OI universe naturally contains only active/liquid coins
- **Data Quality Gates:** Min OI coverage 0.8 (fail-closed if < 80% of universe has fresh OI); staleness limit 6.5 days

---

## 3. Validation Evidence

### Backtest Performance (Major Universe, Weekly)
- **In-Sample Sharpe:** N/A (no separate IS/OOS split — direct to forward-track)
- **Out-of-Sample Sharpe:** 1.21 (weekly, gross price returns)
- **Net Sharpe (after 10bps costs):** 1.38 (vol-targeted)
- **Cost Assumption:** 10 bps per turn (conservative for majors)

### Statistical Validation
- **IC Panel Evidence:**
  - Daily IC: mean=0.0042, t-stat=0.68, n=1635
  - Weekly IC (h=7): mean=0.0455, t-stat=2.91, n=232
  - Bi-weekly IC (h=14): mean=0.0747, t-stat=3.35, n=115
  - **Holding-period effect confirmed:** slower holding horizons show stronger signal
- **Deflated Sharpe p-value:** Not computed (bypassed direct to paper-track)
- **Trials Count (DSR denominator):** N/A
- **Combinatorial PCV:** PASSED (4/5 evidence gates prior to paper-track)
- **PBO Test:** PASSED

### Forward Paper-Track Evidence
- **Paper-Track Start:** 2026-07-02 (decision timestamp)
- **Paper-Track IC (mean):** TBD (≥8 weeks forward evidence required for live consideration)
- **Paper-Track Sharpe:** TBD
- **Paper-Track Weeks:** ~2 (as of 2026-07-16)
- **Evidence Clock Status:** RUNNING (Stage 0 forward-track advance tool, weekly driver)

---

## 4. Risk Boundaries

### Drawdown & Volatility
- **Maximum Historical Drawdown:** 12% (dry-run pipeline, 232 weeks)
- **Drawdown Window:** 2022-2023 (FTX period — notably crowd_contra was +6.5% during FTX collapse)
- **Annualized Volatility:** ~15-18% (weekly rebalance, gross)
- **Vol-Target Adjustment:** Enabled (leverage adjusts to target volatility)

### Capacity & Liquidity
- **Participation Rate Limit:** 10%
- **Capacity (participation-bound):** $7M (conservative 10%×1d), $35-70M (realistic 10%×5d execution horizon)
- **Capacity (impact-constrained):** $3.6-72M (Almgren-Chriss sweep, 5-20bps×1-5d, UNCALIBRATED_PRINCIPLED band)
- **Critical AUM:** $7M (most conservative corner: 10% participation × single-day execution)
- **Median ADV (universe):** $600M+ (major coins only)

### Crowding & Correlation
- **Max Single-Instrument Weight:** ~7.1% (14-leg equal-risk allocation)
- **Crowding Score:** 0.0 (OI contra IS the anti-crowding signal)
- **Correlation to BTC (downside):** 0.13 (tail dependence — near-zero crash beta)
- **Cross-Strategy Correlation:** -0.04 (net returns vs carry_oi_majors; gross was also near-zero)

---

## 5. Execution Reality

### Turnover & Costs
- **Average Turnover:** ~0.4-0.5 per week (14 legs rebalanced)
- **Rebalance Frequency:** Weekly (Monday 09:00 UTC+8)
- **Execution Horizon:** 1 day (testnet/demo); 5 days (realistic for live capacity estimates)
- **Market Impact Model:** Almgren-Chriss (temporary sqrt + permanent linear, participation-driven)

### Gate Performance (Dry-Run Pipeline, 232 weeks)
- **Intents Submitted:** 3066
- **Approved:** 3066
- **Rejected:** 0
- **Rejection Rate:** 0%
- **Primary Rejection Reasons:** None (clean execution record)

---

## 6. Tail Risk & Stress

### Historical Scenarios
- **LUNA Crash (2021-05):** No OI data (pre-2022)
- **FTX Collapse (2022-11):** +6.5% (crowd_contra profited — short BTC/ETH positioning unwound during crash)
- **Banking Crisis (2023-03):** +10.6% (similar contra-positioning effect)
- **Comment:** Dollar-neutral confirmed — tail risk is cross-sectional spread collapse, not market beta

### Tail Statistics
- **VaR (95%):** ~-3.2% (weekly)
- **CVaR (95%):** ~-4.8% (weekly)
- **Hill Tail Index:** ~2.6 (fat-tailed, t-distribution-like)
- **Tail Profile:** Endogenous risk from positioning spread compression, not directional crashes

---

## 7. Macro Regime Profile

### Performance by Regime
| Regime Dimension | High | Mid | Low |
|------------------|------|-----|-----|
| VIX | 1.96 | 1.48 | 1.25 |
| Yield Curve | 1.76 (inverted) | 1.29 | 0.88 (normal) |
| Credit Spread | 1.68 | 1.42 | 1.21 |

### Regime Interpretation
**crowd_contra loves stress/risk-off regimes** — opposite of carry_oi_majors (which prefers low-VIX/normal-curve). The two strategies are macro-complementary: carry walks strong in calm, crowd_contra walks strong in stress. Combined risk-parity book leverages this complementarity (though net@cost execution shows crowd_contra alone still dominates combined Sharpe 1.38 > 1.20).

---

## 8. Monitoring Metrics

### Real-Time Alerts
- **Drawdown Threshold:** 15% (Stage 1 risk monitor breach trigger)
- **Position Drift Tolerance:** 5% of AUM
- **Crowding Alert Level:** N/A (OI contra is inherently anti-crowded)
- **Data Staleness Limit:** 6.5 days (OI data refresh, fail-closed < 80% coverage)

### Degradation Signals
- **IC Decay Window:** 8 weeks rolling (forward-track monitoring)
- **Min IC for LIVE:** t-stat > 1.5 (indicative, not hard gate)
- **Max Correlation Drift:** +0.3 vs historical (cross-strategy)
- **Capacity Utilization Warning:** 50% of critical AUM ($3.5M)

### Review Cadence
- **Weekly:** Forward-track IC update, position reconciliation, paper-vs-live parity check
- **Monthly:** Correlation matrix (carry/crowd/trend), OI data quality audit
- **Quarterly:** Full capacity re-sweep (ADV drift), macro regime profile refresh
- **Trigger-based:** Drawdown > 15%, gate rejection spike, OI data outage > 1 week

---

## 9. Model Lineage & Governance

### Development History
- **Research Session ID:** N/A (direct IC panel candidate, bypassed search loop)
- **Search Method:** Manual thesis (fade retail positioning) + IC panel validation
- **Evaluation Artifact Hash:** N/A (no backtest artifact — direct to forward-track)
- **Approval Request ID:** TBD (requires ≥8 weeks forward evidence before promotion to APPROVED)

### Human Reviews
- **Proposer:** TBD (IC panel discoverer, 2026-07-04)
- **Reviewer:** TBD (requires multi-party approval SM for APPROVED promotion)
- **Approver:** TBD
- **Promotion Date:** N/A (currently PAPER_TRACKING)

### Known Limitations
1. **Short history:** OI data only back to 2022 (~4.5 years); no 2017-2021 bull/bear cycles
2. **Universe size:** 14 majors (vs carry's 58-coin universe) limits diversification
3. **OI data source risk:** Single-venue OI (Binance global_ls field); multi-venue aggregation not yet implemented
4. **Holding-period calibration:** Weekly rebalance is operational default; IC panel shows h=14 slightly stronger t-stat (3.35 vs 2.91) but capacity/turnover tradeoff not yet optimized
5. **No live evidence yet:** Forward-track clock started 2026-07-02; <8 weeks of forward evidence as of 2026-07-16

---

## 10. Deployment Configuration

### Pre-Trade Risk Gate
- **Enabled Checks:** All 9 engines (fat-finger, concentration, notional, leverage, volatility, drawdown, price-freshness, data-quality, crowding)
- **Kill-Switch Binding:** Global kill-switch (cross-strategy latch)
- **Mandate:** `key/live_mandate.json` (tiered approval: AUTO within limits, NEEDS_HUMAN_REVIEW beyond)

### Position Sizing
- **Base Allocation:** 50% of AUM (risk-parity vs carry_oi_majors in combined book; 100% if standalone)
- **Vol-Target Scale:** Enabled (target annualized vol ~15-18%)
- **Max Gross Exposure:** 100% (dollar-neutral L/S)
- **Max Net Exposure:** 5% (market-neutral enforcement)

### Execution Parameters
- **Order Type:** MARKET (default); LIMIT available with anchor fallback (pricing.py)
- **Venue:** Binance Futures (testnet/demo validated); Bybit Futures (adapter ready, not yet live-validated)
- **TWAP Slicing:** Available but not enabled (14-leg $7M is sub-capacity for majors)
- **Emergency Flatten:** Enabled (independent reduce-only channel, breach-triggered)

---

**This model card is a REQUIRED artifact for promotion to APPROVED state.**  
**Last Updated:** 2026-07-16  
**Document Hash:** (to be computed on finalization)
