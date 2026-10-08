export interface DemoIssuerBudget {
  purpose:'submission'|'cancellation';
  reserved_grants:number; signed_grants:number; reserved_orders:number;
  max_grants:number; max_orders:number;
  reserved_notional:string|null; max_notional:string|null; remaining_notional:string|null;
}
interface DemoIssuerReportCommon {
  environment:'BINANCE_USDM_DEMO'; scope:'TECHNICAL_VALIDATION_ONLY';
  observed_at_ms:number; source_commit:string; account_ref_sha256:string;
  valid_from_ms:number; valid_until_ms:number; key_status:'ACTIVE'|'REVOKED'|'MISSING'; key_material_status:'NOT_CHECKED';
  records:number; last_record_at_ms:number|null; journal_sha256:string|null; budgets:DemoIssuerBudget[];
  source_verified:false; submission_enabled:false; cash_reconciled:false; economic_authority:false;
}
export interface DemoIssuerPolicyReport extends DemoIssuerReportCommon {
  schema:'DEMO_ISSUER_STATUS_V1';
  phase:'POLICY_INACTIVE'|'LIMIT_REACHED'|'PENDING_SIGNATURE'|'POLICY_OBSERVED';
  policy_id:string; policy_sha256:string; symbol:'BTCUSDC'|'BTCUSDT';
}
export interface DemoIssuerGroupReport extends DemoIssuerReportCommon {
  schema:'DEMO_ISSUER_STATUS_V2'; symbol:'XAUUSDT';
  phase:'TEMPLATE_INACTIVE'|'GROUP_LIMIT_REACHED'|'GROUP_PENDING_SIGNATURE'|'TEMPLATE_OBSERVED';
  template_id:string; template_sha256:string; budget_basis:'SHARED_GROUP';
  group_id:string; group_binding_sha256:string; continuity_event_sha256:string;
  exit_reserve:{grants:number;orders:number;notional:string};
  opening_remaining:{grants:number;orders:number;notional:string};
}
export type DemoIssuerReport = DemoIssuerPolicyReport | DemoIssuerGroupReport;
export type DemoIssuerSnapshot = {status:'NO_SNAPSHOT'|'DATA_BLOCKED'|'CORRUPT_OR_UNREADABLE';report:null} |
  {status:'AVAILABLE'|'STALE';report:DemoIssuerReport};
