import type {ResearchWorkspaceContext} from './research-workspace';
export interface FactorySpec {transform?:string;id:number; model:string; features:number[]; threshold_bps:number}
export interface FactoryMetrics {execution_feedback?:{profitability_attribution?:ProfitabilityAttribution;evidence_type:string;venue_receipts_verified:false;reconciliation:Record<string,string>;fill_count:number;order_count:number;recent_fills:{time:number;quantity:string;price:string;reference:string;fee:string;reason:string}[];recent_orders:{time:number;status:string;quantity?:string}[];halted_at:number|null;average_exposure:number};decision_counts?:Record<string,number>;order_status_counts?:Record<string,number>;decision_trace?:{time:number;prediction:number;current_weight:number;weight:number;trade_cost:number;risk_penalty:number;funding_cost:number;utility:number;reason:string}[];score:number; net_pnl?:number; drawdown?:number; fees?:number|string; funding?:number|string; fills?:number; equity:{time:number;equity:string}[]}
export interface FactorySession {attempt:number;state:string;command:string;updated:number;config:{research_protocol?:'SINGLE_V1'|'THREE_ROLES_V1';mode:'baseline'|'ai';objective:string;budget:number;attempt:number};result:{selected?:FactorySpec;inner?:FactoryMetrics;outer?:FactoryMetrics;model_hash?:string;reason?:string;limitations?:string[]}}
export interface FactoryTrial {proposal?:{protocol?:string;disagreement?:boolean;roles?:{role:string;candidate_id:number;model:string;elapsed_ms?:number}[];model:string;elapsed_ms?:number;usage?:{total_tokens?:number};request_hash?:string};hypothesis?:{hypothesis_id:string;family_id:string;dataset_hash:string;parent_trial_ids:string[];evaluation_scope:string;evaluation_cutoff_ms:number};ordinal:number;candidate_id:number|null;status:string;spec?:FactorySpec;metrics?:FactoryMetrics;rmse?:number;reason?:string;trial_id:string}
export interface FactoryWorkflow {status:string;stage:string;run_id:string|null;runner_status:string;reason:string;can_resume:boolean}
export interface FactoryPayload extends ResearchWorkspaceContext {sleeve_comparison?:SleeveProcess|null;portfolio_sleeves?:PortfolioResearch|null;research_notes?:FactoryResearchNotes|null;portfolio_research?:PortfolioResearch|null;forward_profitability?:ForwardProfitability|null;profitability?:ProfitabilityStudy|null;source_capture:{captured_at_ms:number;scope:string;records:{source_id:string;status:string;reason?:string;content_sha256?:string}[]}|null;workflow:FactoryWorkflow;resident:{status:string;observed_at_ms:number;checkpoint?:{status:string}}|null;comparison:{schema?:string;ai_group_status:string;rows:{model_calls?:number;model_completed_calls?:number;model_elapsed_ms?:number|null;model_total_tokens?:number|null;fold:number;group:string;status:string;selected?:FactorySpec;outer?:FactoryMetrics;portfolio?:Pick<PortfolioResearch,'selected'|'baseline'|'decision'|'comparisons'|'inner_trials_attempted'>;trials:unknown[]}[]}|null;pipeline:{status:string;stage:string;updated_ms:number;reason?:string}|null;verification:{artifact_sha256:string;verification:{status:string;economic_eligible:false;reason?:string}}|null;assessment:FactoryAssessment|null;forward:FactoryForward|null;sessions:FactorySession[];selected:FactorySession|null;trials:FactoryTrial[];events:{seq:number;at:number;kind:string;payload:Record<string,unknown>}[];provider:{status:'NOT_CONFIGURED'|'CONFIGURED_UNVERIFIED'|'RECENT_CALL_SUCCEEDED';model:string;connected:boolean};errors:string[];authoritative:false;snapshot_status:string;data_provenance:string;read_only:true;as_of_ms:number}

export interface CausalCollection {slot_ms:number;status:string;request_attempts?:number|null;recovered_requests?:number|null;elapsed_ms?:number|null;failures:{quote:string;kind?:string;reason?:string;category?:string;http_status?:number|null;retry_after_seconds?:number|null}[]}
export interface PortfolioCandidate {id:number;component_ids:number[];quarters:number[]}
export interface PortfolioResearch {
 construction?:string;
 forward?: (Omit<FactoryForward,'recent_forecasts'> & {reason?:string;recent_forecasts:{slot_ms:number;issued_at_ms:number;prediction:number;baseline_prediction?:number;component_predictions?:number[];target_weight:null}[]})|null;
 forward_cost?:ForwardCostAccount|null;status:string;stage?:string;reason?:string;decision?:string;selected?:PortfolioCandidate;baseline?:PortfolioCandidate;components?:FactorySpec[];inner_trials_attempted?:number;observed_at_ms?:number;quote?:string;model_hash?:string;timings?:Record<string,number>;inner_trials?:{spec:PortfolioCandidate;status:string;reason?:string;metrics?:ProfitabilityMetrics;trial_id?:string}[];outer_cases?:{name:string;status:string;fee_bps:number;slippage_bps:number;metrics?:ProfitabilityMetrics&{sleeve_execution?:SleeveExecution;equity?:{time:number;equity:string}[]}}[];comparisons?:{fee_bps:number;slippage_bps:number;net_pnl_delta:number;portfolio_net_pnl:number;baseline_net_pnl:number;portfolio_drawdown:number;baseline_drawdown:number;portfolio_fills:number;baseline_fills:number}[];economic_eligible:false;authoritative:false}
export interface CausalInformationEvidence {collection?:CausalCollection|null;usdt_study?:CausalInformationEvidence['study'];status:string;observed_at_ms:number;start_ms?:number;mature_ms?:number;reason?:string;detail?:string;quotes?:{quote:string;status:string;ready_rows:number;required_rows:number;missing_slots:number[]}[];study?:{status:string;reason?:string;results?:Record<string,{status:string;decision:string;trials_attempted:number;selected_id:number;cases:{id:number;fee_bps:number;slippage_bps:number;metrics:ProfitabilityMetrics}[]}>}|null;prediction_evidence?:'NONE_FEATURE_COLLECTION_ONLY';economic_eligible:false}

export interface FactoryForecast {slot_ms:number;issued_at_ms:number;prediction:number;target_weight:number;model_hash:string;entry_ms:number;exit_ms:number}
export interface FactoryLabel {slot_ms:number;prediction:number;realized_return:number;residual:number;available_at_ms:number}
export interface FactoryForward {status:string;observed_at_ms:number;activated_at_ms?:number;predictions?:number;labels?:number;pending?:number;recent_forecasts?:FactoryForecast[];recent_labels?:FactoryLabel[];diagnostics?:{rmse:number|null;mean_error:number|null;direction_accuracy:number|null;drift:string}}

export interface FactoryAssessment {cases:{case:string;slippage_bps:number;fee_bps:number;delay_hours:number;metrics:FactoryMetrics}[];portfolio:{status:string;prediction_correlation?:number|null;prediction_disagreement_fraction?:number;capital_model?:string;capacity_scenario?:string;add_net_pnl_delta?:number;replace_net_pnl_delta?:number;add?:FactoryMetrics;replace?:FactoryMetrics};return_diagnostics:{status:string;mean_ci?:number[];worst_day?:number;tail_mean_5pct?:number};search_diagnostics:{status:string;family_tail_fraction?:number;trials_attempted?:number};authoritative:false}

export interface ProfitabilityAttribution {status:string;reason:string|null;economic_eligible:false;diagnosis:string;net_pnl:string|null;gross_reference_pnl:string|null;embedded_slippage:string|null;fees:string|null;funding:string|null;explicit_spread?:string;explicit_impact?:string;turnover_notional:string|null;turnover_initial_equity:string|null;position_contribution:{long:string;short:string}|null;actual_venue_fees:null;spread_component:null;reconciliation_residual?:string}

export interface ProfitabilityStudy {p5_queue?:PassiveExecutionStudy|null;causal?:CausalInformationEvidence|null;inputs?:ProfitabilityInputVersion[];p5_calibration?:ExecutionCalibration|null;p6?:ForwardCostAccount|null;p3_study?:ProfitabilityStudy["p4"];p4?:{status:string;trials_attempted:number;selected_id:number;decision:string;cases:{id:number;fee_bps:number;slippage_bps:number;metrics:ProfitabilityMetrics;state_controls:{matched_rows:number;within_state_covariance:number|null}}[]}|null;p3?:{status:string;research_status:string;quotes:{quote:string;feature:{basis:number;index_premium:number;decision_ms:number}}[];failures:{quote:string;reason:string}[]}|null;p5?:{status:string;paired_orders:number;quantity_absolute_error:string;state_time_absolute_error_ms:number;venue_receipts_verified:false}|null;p2?:{decision:string;pairs:{fee_bps:number;slippage_bps:number;effect:{status:string;wait_decisions?:number;timeouts?:number;delta_wait_minus_immediate?:Record<string,string>}}[]}|null;p0:{metrics:ProfitabilityMetrics;decision:string}|null;p1:{selected_policy:string;decision:string;trials_attempted:number;trials:{policy:string;status:string;metrics?:ProfitabilityMetrics;reason?:string}[];cases:{policy:string;fee_bps:number;slippage_bps:number;metrics:ProfitabilityMetrics}[]}|null;failures:{stage:string;reason:string}[];authoritative:false}

export interface ForwardProfitability {status:string;reason?:string;common_mature_labels?:number;missing_forecast_slots?:number[];missing_mature_label_slots?:number[];pending_slots?:number[];unmatched_label_slots?:number[];cost_adjusted_pnl?:null;economic_eligible:false}

export interface ProfitabilityMetrics {score?:number;net_pnl?:number;drawdown?:number;fees?:string|number;funding?:string|number;fills?:number;decision_policy?:string;execution_feedback?:{profitability_attribution?:ProfitabilityAttribution;reconciliation?:Record<string,string>;fill_count?:number;order_count?:number}}

export interface ForwardCostAccount {last_completed?:ForwardCostAccount;status:string;reason?:string;window_start_ms?:number;window_end_ms?:number;missing_slots?:number[];mature_at_ms?:number;window?:{window_start_ms:number;window_end_ms:number;mature_days:number;planned_days:number};cases?:{model:string;fee_bps:number;slippage_bps:number;attribution:ProfitabilityAttribution;sleeve_execution?:SleeveExecution;equity:{time:number;equity:string}[]}[];economic_eligible:false;actual_venue_costs?:null}

export interface ProfitabilityInputVersion {kind:string;revision:number;input_hash:string;operator:string;created_ms:number;result_status?:string;trials_attempted?:number}
export interface ExecutionCalibration {status:string;selected_profile:number;decision:string;trials_attempted:number;venue_receipts_verified:false;trials:{profile:number;order_latency_ms:number;cancel_latency_ms:number;status:string;reason?:string;fit?:{quantity_absolute_error:string;state_time_absolute_error_ms:number;unmatched_events:number}}[];heldout:{paired_orders:number;quantity_absolute_error:string;state_time_absolute_error_ms:number}}

export interface FactoryResearchNotes {
 role_runs?:{definition:string;protocol:string;calls:{role:string;status:string;reason:string|null;elapsed_ms:number|null;candidate_id:number|null}[]}[];
 verdict:{scope_decisions?:Record<string,string>;schema:string;scope:'OUTER_EXPLANATION';decision:string;known_at_ms:number;facts:{id:string;label?:string;value:number|string|null;unit:string;status:string;evidence_hash:string|null;pointer:string|null}[];cost_scenarios:{fee_bps:number;slippage_bps:number}[];source_hashes:string[];model_hashes:string[];profitability_established:false;search_feedback_allowed:false;economic_eligible:false;authoritative:false}|null;
 episodes:{episode_id:string;known_at_ms:number;market_max_ms:number;scope:string;experiment_hash:string;snapshot_hash:string;partition_hash:string;evidence_hash:string;parent_ids:string[];summary:{candidate_id:number|null;status:string;score:number|null;reason?:string}}[];
 history:{episodes:number;head_hash:string;last_known_at_ms:number}|null;
 scope:'HUMAN_READ_ONLY';outer_results_available_to_search:false;authoritative:false;
}

export interface PassiveQueueOrder {order_id:string;side:string;price:string;quantity:string;filled_quantity:string;remaining_quantity:string;status:string;reason:string|null;censored:boolean;queue_ahead_at_arrival:string|null}
export interface PassiveQueueEvent {order_id:string;status:string;venue_time_ms:number|null;client_receive_ms:number|null;client_time_ms:number|null;quantity:string|null;price:string|null;reason:string|null}
export interface SimulatedLiquidityFeeModel {scope:'EXPLICIT_SIMULATION_ASSUMPTION';actual_venue_fees:null;
 mode:'FIXED'|'BY_FILL_LIQUIDITY_ROLE';maker_fee_rate:string;taker_fee_rate:string;reservation_fee_rate:string;
 role_source:'EXECUTION_MECHANISM_NOT_ORDER_TYPE'}
export interface PassiveAccountEvidence {input_hash:string;result_hash:string;ledger_applied:true;actual_venue_fees:null;simulated_fee_rate:string|null;fee_model?:SimulatedLiquidityFeeModel;
 reconciliation:Record<'initial_equity'|'ending_equity'|'price_pnl'|'fee'|'spread'|'impact'|'funding'|'liquidation_fee'|'insurance_adjustment'|'adl_adjustment'|'attributed_change'|'residual',string>;
 equity_history:{timestamp:number;equity:string;drawdown:string}[];history_count:number;
 ending_state:{positions:Record<string,{quantity:string}>;margin_admission_policy?:string}}
export interface FrozenAccountTerms {
 schema:'BINANCE_USDM_ACCOUNT_TERMS_V1';input_hash:string;account_ref_sha256:string;
 provenance:'SYNTHETIC_TEST_FIXTURE'|'IMPORTED_UNVERIFIED';source_verified:false;actual_paid_fees:null;
 symbol:string;quote:string;selected_leverage:number;symbol_config_notional_cap:string;new_risk_notional_cap:string;
 cap_comparison:'STRICTLY_BELOW';initial_margin_rounding:'LEVERAGE_RECIPROCAL_CEILING_1E24_V1';overflow_policy:'LAST_BRACKET_RISK_ESTIMATE_V1';
 maker_fee_rate:string;taker_fee_rate:string;rpi_fee_rate_not_applied:string|null;limitations:string[];
 observations:Record<'before'|'after',Record<string,{requested_at_ms:number;received_at_ms:number;response_sha256:string}>>;
 margin_brackets:{notional_floor:string;notional_cap:string;initial_margin_rate:string;maintenance_margin_rate:string;maintenance_amount:string;maximum_initial_leverage:number}[];
}
export interface PassiveReceiptComparison {
 start_ms:number;end_ms:number;paired_orders:number;submitted_orders:number;observed_events:number;simulated_events:number;
 matched_events:number;unmatched_events:number;unmatched_orders:string[];unobserved_orders:string[];
 coverage_gaps:{start_ms:number;end_ms:number}[];final_quantity_absolute_error:string;
 matched_event_quantity_absolute_error:string;quantity_path_error_btc_ms:string;increment_quantity_absolute_error:string;
 carry_quantity_absolute_error:string;venue_time_absolute_error_ms:number;receive_time_absolute_error_ms:number;
 state_mismatch_duration_ms:number;order_rows_total:number;
 rows:{order_id:string;side:string;quantity:string;observed_state:string;simulated_state:string;
 observed_censored:boolean;simulated_censored:boolean;final_quantity_absolute_error:string;quantity_path_error_btc_ms:string;
 venue_time_absolute_error_ms:number;receive_time_absolute_error_ms:number;path_points_total:number;
 path:{received_ms:number;observed_filled:string;simulated_filled:string}[]}[];
}
export interface NativeOrderReceiptEvidence {
 schema:'BINANCE_USDM_ORDER_RECEIPTS_V1';input_hash:string;account_ref_sha256:string;symbol:string;quote:string;
 provenance:'SYNTHETIC_TEST_FIXTURE'|'IMPORTED_UNVERIFIED';source_verified:false;actual_paid_fees:null;
 messages_count:number;unique_messages:number;duplicate_messages:number;trades_count:number;missing_commission_trades:number;
 all_commissions_in_quote:boolean;reported_commission_by_asset:Record<string,string>;reported_quote_commission:string|null;
 reported_filled_quantity:string;unobserved_order_ids:string[];coverage:{start_ms:number;end_ms:number;gaps:{start_ms:number;end_ms:number}[]};
 trade_rows_total:number;artifact:string;projection:string;economic_eligible:false;limitations:string[];
 fee_comparisons:{profile_ms:number;simulated_fee:string|null;reported_minus_simulated_fee:string|null;
 comparison_available:boolean;comparison_scope:string;source_verified:false;actual_paid_fees:null}[];
 trade_rows:{order_id:string;venue_order_id:string;trade_id:string;event_ms:number;transaction_ms:number;venue_trade_ms:number;
 received_at_ms:number;quantity:string;price:string;liquidity_role:'MAKER'|'TAKER';reported_commission:string|null;commission_asset:string|null;response_sha256:string}[];
}
export interface PassiveReceiptCalibration {
 status:string;selected_profile_ms:number|null;equally_best_profiles_ms:number[];cutoff_ms:number;
 window_start_ms:number;window_end_ms:number;partition_clock:'ACTUAL_CLIENT_RECEIPT_TIME';trials_attempted:number;
 observed_provenance:{kind:string;declared_source_sha256:string};
 native_observations?:NativeOrderReceiptEvidence;
 trials:{profile_ms:number;status:string;reason?:string;fit?:PassiveReceiptComparison}[];
 heldout:PassiveReceiptComparison|null;calibration_complete:false;venue_receipts_verified:false;queue_model_calibrated:false;
}
export interface VenueOrderBoundaryEvidence {
 scope:'ISOLATED_DECLARED_ORDERS_ONLY';percent_price_up:string;percent_price_down:string;
 max_open_orders:number;market_take_bound:string;
 limit_band_reference:'CURRENT_MARK_AT_ADMISSION';market_notional_reference:'CURRENT_MARK_AT_ADMISSION';
 market_protection:'CURRENT_MARK_AT_EACH_FILL_REJECT_REMAINDER_OUTSIDE_BOUND';venue_behavior_verified:false;
}
export interface VenueOrderFilterEvidence {
 schema:'BINANCE_USDM_DECLARED_FILTERS_V1';
 price:{minimum:string;maximum:string;step:string};lot_size:{minimum:string;maximum:string;step:string};
 market_lot_size:{minimum:string;maximum:string;step:string};quantity_atom:string;
 grid_origin:'FILTER_MINIMUM';market_application:'LOT_SIZE_AND_MARKET_LOT_SIZE';
 zero_price_fields:'DISABLE_INDIVIDUAL_CONSTRAINT';partial_fills:'ACCOUNTING_ATOM_NOT_PARENT_ORDER_GRID';
 venue_behavior_verified:false;
}
export interface PassiveExecutionStudy {status:string;symbol:string;quote:string;start_ms:number;end_ms:number;trials_attempted:number;selected_profile:number|null;ledger_applied:boolean;
 calibration?:PassiveReceiptCalibration|null;simulations_attempted?:number;input_sha256?:string;full_window_trials_attempted?:number;
 account_environment?:{symbol:string;quote:string;venue_margin_tiers_verified:false;actual_venue_fees:null;limitations:string[];venue_order_constraints?:VenueOrderBoundaryEvidence|null;venue_order_filters?:VenueOrderFilterEvidence|null;account_terms?:FrozenAccountTerms|null}|null;
 quality:{counts:{depth_updates:number;aggregate_trades:number;mark_updates:number;duplicates:number};failures:{reason:string}[];book_samples:{available_at_ms:number;transaction_time_ms:number;bids:[string,string][];asks:[string,string][]}[]};
 trials:{profile_ms:number;status:string;reason?:string;fill_count?:number;censored_orders?:number;event_count?:number;orders?:PassiveQueueOrder[];events?:PassiveQueueEvent[];account?:PassiveAccountEvidence|null}[]}

export interface SleeveExecution {
 construction:string;ending:{component_ids:number[];quarters:number[];positions:Record<string,string>;desired_weights:Record<string,string>;actual_quantity:string};
 decision_count:number;execution_count:number;trace_hash:string;
 decisions:{time:number;positions:Record<string,string>;desired_weights:Record<string,string>;gross_desired_weight:string;net_desired_weight:string;actual_quantity:string;target:number|null;choices:{component_id:number;reason:string;raw_prediction?:number;current_weight?:number;target?:number|null}[]}[];
 execution:{time:number;status:string;quantity:string;equity:string;reason:string;positions:Record<string,string>;internal_reallocation:boolean}[];
}

export interface SleeveProcess {status:string;new_model_calls?:number;refitted_models?:number;failed_groups?:number;completed_groups?:number;total_groups?:number;fold?:number;group?:string;rows?:{fold:number;group:string;status:string;stage?:string;reason?:string;test_start_ms?:number;test_end_ms?:number;inner_trials_attempted:number;outer_cases_attempted:number;failed_cases:number;portfolio?:Pick<PortfolioResearch,'selected'|'baseline'|'decision'|'comparisons'|'inner_trials_attempted'>}[]}
