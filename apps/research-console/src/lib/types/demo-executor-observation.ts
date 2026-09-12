export const executorComponents = ['private_stream','origin_BTCUSDC','origin_BTCUSDT','resident','oms','cancellation'] as const;
export type ExecutorComponent = typeof executorComponents[number];
export interface DemoExecutorObservationReport {
  schema:'DEMO_EXECUTOR_OBSERVATION_V1'; environment:'BINANCE_USDM_DEMO'; scope:'TECHNICAL_VALIDATION_ONLY';
  phase:'PARTIAL_RECORDS'|'RECORDS_OBSERVED'; observed_at_ms:number; account_ref_sha256:string; source_commit:string;
  source_verified:false; submission_enabled:false; cash_reconciled:false; economic_authority:false;
  components:Array<{name:ExecutorComponent; status:'AVAILABLE'|'NO_SNAPSHOT'|'CORRUPT_OR_UNREADABLE';
    phase:string|null; observed_at_ms:number|null; report_sha256:string|null;
    counts:{records:number; messages:number; unique_messages:number; gaps:number}|null}>;
}
export type DemoExecutorObservationSnapshot = {status:'AVAILABLE'|'STALE'; report:DemoExecutorObservationReport}|
  {status:'NO_SNAPSHOT'|'DATA_BLOCKED'|'CORRUPT_OR_UNREADABLE'; report:null};
