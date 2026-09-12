export interface DemoResidentStatusReport {
  schema:'DEMO_RESIDENT_STATUS_V1'; environment:'BINANCE_USDM_DEMO'; scope:'TECHNICAL_VALIDATION_ONLY';
  phase:'NO_RESERVATIONS'|'INPUTS_UNRESOLVED'|'WAITING_QUERY_INTERVAL'|'RESIDENT_BUSY'|
    'QUERY_RECOVERY_REQUIRED'|'QUERY_OBSERVED_RECEIPTS_REQUIRED'|'OMS_TERMINAL_CASH_SEPARATE';
  reason:string|null; observed_at_ms:number; executor_boot_sha256:string; source_commit:string;
  attempt_count:number|null; last_attempt_at_ms:number|null; next_query_at_ms:number|null; reservation_sha256:string|null;
  query_status:'QUERY_RECOVERY_REQUIRED'|'ORDER_SNAPSHOTS_OBSERVED'|null; query_reason:string|null; query_sha256:string|null;
  resolution_status:'UNRESOLVED'|'READY_FOR_OMS_PROJECTION'|'OMS_TERMINAL_PROJECTED'|null;
  resolution_issues:string[]; resolution_sha256:string|null; report_sha256:string;
  source_verified:false; new_order_submission_enabled:false; economic_authority:false; cash_reconciled:false;
}
export type DemoResidentStatusSnapshot = {status:'AVAILABLE'; report:DemoResidentStatusReport; last_result:DemoResidentStatusReport|null}|
  {status:'NO_SNAPSHOT'|'DATA_BLOCKED'|'CORRUPT_OR_UNREADABLE'; report:null; last_result:null};
