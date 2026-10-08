# Model Card: {strategy_id}

**Version:** 1.0  
**Status:** {status}  
**Generated:** {generated_at}

---

## 1. Model Identity

- **Strategy ID:** `{strategy_id}`
- **Signal Expression:** `{signal_expr}`
- **Asset Class:** {asset_class}
- **Universe:** {universe_description}
- **Holding Period:** {holding_period}

---

## 2. Training Data & Sample

### Data Coverage
- **Start Date:** {train_start}
- **End Date:** {train_end}
- **Total Days:** {n_days}
- **Instruments:** {n_instruments}

### Universe Construction
- **Selection Criteria:** {universe_criteria}
- **Survivorship Bias:** {survivorship_handling}
- **Data Quality Gates:** {data_quality_gates}

---

## 3. Validation Evidence

### Backtest Performance
- **In-Sample Sharpe:** {is_sharpe}
- **Out-of-Sample Sharpe:** {oos_sharpe}
- **Net Sharpe (after costs):** {net_sharpe}
- **Cost Assumption:** {cost_bps} bps per turn

### Statistical Validation
- **Deflated Sharpe p-value:** {deflated_sharpe_pvalue}
- **Trials Count (DSR denominator):** {catalog_trial_count}
- **Combinatorial PCV:** {pcv_result}
- **PBO Test:** {pbo_result}

### Forward Paper-Track Evidence
- **Paper-Track Start:** {paper_track_start}
- **Paper-Track IC (mean):** {paper_track_ic_mean}
- **Paper-Track Sharpe:** {paper_track_sharpe}
- **Paper-Track Weeks:** {paper_track_weeks}

---

## 4. Risk Boundaries

### Drawdown & Volatility
- **Maximum Historical Drawdown:** {max_drawdown_pct}%
- **Drawdown Window:** {max_dd_window}
- **Annualized Volatility:** {annualized_vol_pct}%
- **Vol-Target Adjustment:** {vol_target_enabled}

### Capacity & Liquidity
- **Participation Rate Limit:** {participation_rate_pct}%
- **Capacity (participation-bound):** ${capacity_participation_m}M
- **Capacity (impact-constrained):** ${capacity_impact_m}M
- **Critical AUM:** ${critical_aum_m}M (conservative)
- **Median ADV (universe):** ${median_adv_m}M

### Crowding & Correlation
- **Max Single-Instrument Weight:** {max_instrument_weight_pct}%
- **Crowding Score:** {crowding_score}
- **Correlation to BTC (downside):** {btc_tail_dependence}
- **Cross-Strategy Correlation:** {cross_strategy_corr}

---

## 5. Execution Reality

### Turnover & Costs
- **Average Turnover:** {avg_turnover}
- **Rebalance Frequency:** {rebalance_freq}
- **Execution Horizon:** {execution_horizon_days} days
- **Market Impact Model:** {impact_model}

### Gate Performance
- **Intents Submitted:** {n_intents_total}
- **Approved:** {n_approved}
- **Rejected:** {n_rejected}
- **Rejection Rate:** {rejection_rate_pct}%
- **Primary Rejection Reasons:** {top_rejection_reasons}

---

## 6. Tail Risk & Stress

### Historical Scenarios
- **LUNA Crash (2021-05):** {luna_pnl}% ({luna_comment})
- **FTX Collapse (2022-11):** {ftx_pnl}% ({ftx_comment})
- **Banking Crisis (2023-03):** {banking_pnl}% ({banking_comment})

### Tail Statistics
- **VaR (95%):** {var_95}%
- **CVaR (95%):** {cvar_95}%
- **Hill Tail Index:** {hill_tail_index}
- **Tail Profile:** {tail_profile_comment}

---

## 7. Macro Regime Profile

### Performance by Regime
| Regime Dimension | High | Mid | Low |
|------------------|------|-----|-----|
| VIX | {vix_high_sharpe} | {vix_mid_sharpe} | {vix_low_sharpe} |
| Yield Curve | {curve_steep_sharpe} | {curve_mid_sharpe} | {curve_inverted_sharpe} |
| Credit Spread | {credit_wide_sharpe} | {credit_mid_sharpe} | {credit_tight_sharpe} |

### Regime Interpretation
{regime_interpretation_text}

---

## 8. Monitoring Metrics

### Real-Time Alerts
- **Drawdown Threshold:** {dd_alert_threshold}%
- **Position Drift Tolerance:** {position_drift_tolerance}%
- **Crowding Alert Level:** {crowding_alert_level}
- **Data Staleness Limit:** {staleness_limit_hours}h

### Degradation Signals
- **IC Decay Window:** {ic_decay_window} weeks
- **Min IC for LIVE:** {min_ic_live}
- **Max Correlation Drift:** {max_corr_drift}
- **Capacity Utilization Warning:** {capacity_warning_pct}%

### Review Cadence
- **Weekly:** Paper-track IC, position reconciliation
- **Monthly:** Correlation matrix, capacity usage, crowding
- **Quarterly:** Full re-evaluation (OOS extension), regime profile update
- **Trigger-based:** Drawdown breach, data quality failure, gate rejection spike

---

## 9. Model Lineage & Governance

### Development History
- **Research Session ID:** {research_session_id}
- **Search Method:** {search_method}
- **Evaluation Artifact Hash:** {evaluation_artifact_hash}
- **Approval Request ID:** {approval_request_id}

### Human Reviews
- **Proposer:** {proposer_actor}
- **Reviewer:** {reviewer_actor}
- **Approver:** {approver_actor}
- **Promotion Date:** {promotion_date}

### Known Limitations
{known_limitations_text}

---

## 10. Deployment Configuration

### Pre-Trade Risk Gate
- **Enabled Checks:** {enabled_gate_checks}
- **Kill-Switch Binding:** {kill_switch_binding}
- **Mandate:** {mandate_file}

### Position Sizing
- **Base Allocation:** {base_allocation_pct}% of AUM
- **Vol-Target Scale:** {vol_target_scale}
- **Max Gross Exposure:** {max_gross_exposure_pct}%
- **Max Net Exposure:** {max_net_exposure_pct}%

### Execution Parameters
- **Order Type:** {order_type}
- **Venue:** {venue}
- **TWAP Slicing:** {twap_enabled}
- **Emergency Flatten:** {emergency_flatten_enabled}

---

**This model card is a REQUIRED artifact for promotion to APPROVED state.**  
**Last Updated:** {last_updated}  
**Document Hash:** {document_hash}
