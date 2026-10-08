import type {ResearchWorkspaceContext} from './research-workspace';
export interface CampaignConfig {research_protocol?:'SINGLE_V1'|'THREE_ROLES_V1';campaign_id:number;objective:string;mode:string;budget:number;max_cycles:number;interval_hours:number;source_ids?:string[];market?:'XAUUSDT';input_id?:number;input_sha256?:string;execution_feedback_sha256?:string;execution_feedback_mode?:'after_first_round'}
export interface Campaign {campaign_id:number;config:CampaignConfig;state:string;created_ms:number;wait_reason:string;next_due_ms:number|null}
export interface CampaignRound {campaign_id:number;ordinal:number;attempt:number;state:string;created_ms:number;finished_ms:number|null}
export interface ModelDiagnostics {
 status:string;count:number;recommendation:string;rmse:number|null;zero_baseline_rmse:number|null;mse_delta:number|null;
 mae:number|null;bias:number|null;directional_accuracy:number|null;directional_count:number;
 calibration:{lower:number|null;upper:number|null;count:number;prediction:number|null;actual:number|null;rmse:number|null}[];
 observations:{time:number;prediction:number;actual:number;residual:number}[];
 daily:{time:number;count:number;rmse:number;baseline_rmse:number}[];
 overlapping_labels:true;economic_eligible:false;
}
export interface LifecycleModel {attempt:number;model_hash:string;spec:{model:string;transform?:string};state:string;labels:number;predictions:number;updated_ms:number|null;diagnostics:ModelDiagnostics}
export interface LifecycleSource {id:string;name:string;category:string;url:string;adopt:string;limitation:string}
export interface GoldInputSnapshot {input_id:number;input_sha256:string;bytes:number;start_ms:number;end_ms:number;frame_count:number;decision_count:number;source_verified:false}
export interface GoldExecutionReview {status:string;records:{feedback_sha256:string;schema:string;available_at_ms:number;status:string;source_verified:false;position_flat:boolean|null;next_attempts:{attempt:number;state:string}[]}[]}
export interface GoldResearchResult {execution_review:GoldExecutionReview;attempt:number;state:string;candidate:{id:number;sell_threshold:string;confirm_windows:number}|null;trial_id:string|null;report_status:string;outcome:string|null;costs:Record<string,string>|null;recorded_verification_status:string|null;latest_review:{status:string;checked_at_ms:number|null;current:boolean}|null;economic_eligible:false;authoritative:false}
export interface GoldBarProgramStatus {program_id:number;planned_rounds:number;window_bars:number;completed_rounds:number;recorded_stage:string|null;recorded_at_ms:number|null;terminal_status:string|null;error_type:string|null;liveness:"UNKNOWN";economic_eligible:false;submission_enabled:false}
export interface GoldBarProgramCatalog {status:"NO_SNAPSHOT"|"AVAILABLE"|"DATA_BLOCKED";items:GoldBarProgramStatus[];errors:string[];truncated:boolean}
export interface FactoryLifecyclePayload extends ResearchWorkspaceContext {gold_bar_programs?:GoldBarProgramCatalog;collection_programs:{root_id:number;max_batches:number;state:string;batches:{root_id:number;ordinal:number;campaign_id:number;input_id:number}[]}[];collection_requests:{campaign_id:number;request_hash:string;observation?:GoldCollectionObservation;request:{config:CampaignConfig;input_id:number;seconds:number;operator:string;decision_policy?:"FIXED_TEMPLATE_RETROSPECTIVE_WINDOW_V1"|"FIRST_RECEIVED_WATERMARK_V1"|"AI_DESIGNED_WATERMARK_V1"|"AI_DESIGNED_ZONE_V1"}}[];gold_results:GoldResearchResult[];gold_inputs:{status:"AVAILABLE"|"NO_SNAPSHOT"|"DATA_BLOCKED";items:GoldInputSnapshot[];errors:string[];truncated:boolean};worker:{status:string;observed_at_ms:number}|null;campaigns:Campaign[];rounds:CampaignRound[];models:LifecycleModel[];comparisons:{left:number;right:number;shared_labels:number;mse_delta:number|null;status:string}[];sources:LifecycleSource[];capabilities:{id:string;title:string;status:string;detail:string}[];scheduler_enabled:boolean;errors:string[];as_of_ms:number;read_only:true;authoritative:false}

export interface GoldCollectionObservation {status:string;run_id:string|null;reason:string;research_created:boolean;authoritative:false}
