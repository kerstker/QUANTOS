export interface CampaignConfig {campaign_id:number;objective:string;mode:string;budget:number;max_cycles:number;interval_hours:number;source_ids:string[]}
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
export interface FactoryLifecyclePayload {worker:{status:string;observed_at_ms:number}|null;campaigns:Campaign[];rounds:CampaignRound[];models:LifecycleModel[];comparisons:{left:number;right:number;shared_labels:number;mse_delta:number|null;status:string}[];sources:LifecycleSource[];capabilities:{id:string;title:string;status:string;detail:string}[];scheduler_enabled:boolean;errors:string[];as_of_ms:number;read_only:true;authoritative:false}
