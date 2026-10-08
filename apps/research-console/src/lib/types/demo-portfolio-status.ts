export const portfolioStatuses=['NO_CHANGE','QUERY_ONLY','TASK_READY','EXECUTION_ATTEMPT_RECORDED',
  'EXECUTION_NOT_COMPLETED','CASH_RECONCILIATION_REQUIRED','CASH_RECONCILED','INPUTS_UNRESOLVED',
  'CONTROLLER_RESULT_NOT_RETAINED','ALREADY_FLAT','CLOSE_RECOVERY_REQUIRED','CLOSING_REQUIRED',
  'DECISION_BUDGET_RESERVED','EXPIRED_QUERY_ONLY','CYCLE_DEADLINE_REACHED'] as const;
export type PortfolioStatus=typeof portfolioStatuses[number];
export interface DemoPortfolioReport {
  schema:'DEMO_PORTFOLIO_STATUS_V1';environment:'BINANCE_USDM_DEMO';scope:'TECHNICAL_VALIDATION_ONLY';
  source_commit:string;observed_at_ms:number;status:PortfolioStatus;phase:string;
  allocation_sha256:string|null;model_hash:string|null;capital:string|null;policy_valid_until_ms:number|null;
  task_sha256:string|null;close_index:number|null;final_quantity:string|null;fill_count:number|null;
  cash_status:'MATCHED_REPORTED_INPUTS'|null;cash_report_sha256:string|null;wallet_residual:string|null;
  cash_reconciled:boolean;economic_authority:false;submission_enabled:false;
}
export type DemoPortfolioSnapshot={status:'AVAILABLE'|'STALE';report:DemoPortfolioReport}|
  {status:'NO_SNAPSHOT'|'DATA_BLOCKED'|'CORRUPT_OR_UNREADABLE';report:null};
