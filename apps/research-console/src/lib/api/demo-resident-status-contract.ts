const object = (v:unknown):v is Record<string,unknown> => !!v && typeof v==='object' && !Array.isArray(v);
const hash = (v:unknown) => typeof v==='string' && /^[a-f0-9]{64}$/.test(v);
const integer = (v:unknown):v is number => typeof v==='number' && Number.isSafeInteger(v) && v>=0;
const time = (v:unknown):v is number => integer(v) && v>0;
const reason = (v:unknown) => typeof v==='string' && /^[A-Z][A-Z0-9_]{0,100}$/.test(v);
const fields = `schema environment scope phase reason observed_at_ms executor_boot_sha256 source_commit
  attempt_count last_attempt_at_ms next_query_at_ms reservation_sha256 query_status query_reason query_sha256
  resolution_status resolution_issues resolution_sha256 report_sha256 source_verified new_order_submission_enabled
  economic_authority cash_reconciled`.split(/\s+/);
function reportValid(r:unknown, asOf:number):boolean {
  if (!object(r) || Object.keys(r).length!==fields.length || !fields.every(k=>k in r) ||
    r.schema!=='DEMO_RESIDENT_STATUS_V1' || r.environment!=='BINANCE_USDM_DEMO' || r.scope!=='TECHNICAL_VALIDATION_ONLY' ||
    !['NO_RESERVATIONS','INPUTS_UNRESOLVED','WAITING_QUERY_INTERVAL','RESIDENT_BUSY','QUERY_RECOVERY_REQUIRED',
      'QUERY_OBSERVED_RECEIPTS_REQUIRED','OMS_TERMINAL_CASH_SEPARATE'].includes(String(r.phase)) ||
    !time(r.observed_at_ms) || r.observed_at_ms>asOf || !hash(r.executor_boot_sha256) || !hash(r.report_sha256) ||
    typeof r.source_commit!=='string' || !/^[a-f0-9]{40}$/.test(r.source_commit) ||
    !['source_verified','new_order_submission_enabled','economic_authority','cash_reconciled'].every(k=>r[k]===false) ||
    !['reason','query_reason'].every(k=>r[k]===null || reason(r[k])) ||
    !['reservation_sha256','query_sha256','resolution_sha256'].every(k=>r[k]===null || hash(r[k]))) return false;
  if (r.attempt_count===null || r.attempt_count===0) {
    if (r.last_attempt_at_ms!==null || r.next_query_at_ms!==null) return false;
  } else if (!integer(r.attempt_count) || r.attempt_count<1 || r.attempt_count>10000 || !time(r.last_attempt_at_ms) ||
    !time(r.next_query_at_ms) || r.last_attempt_at_ms>r.observed_at_ms || r.next_query_at_ms!==r.last_attempt_at_ms+30000) return false;
  if (r.phase==='WAITING_QUERY_INTERVAL' && (!time(r.next_query_at_ms) || r.next_query_at_ms<=r.observed_at_ms)) return false;
  if (![null,'QUERY_RECOVERY_REQUIRED','ORDER_SNAPSHOTS_OBSERVED'].includes(r.query_status as string|null) ||
    (r.query_status===null)!==(r.query_sha256===null) || r.query_status===null && r.query_reason!==null ||
    ![null,'UNRESOLVED','READY_FOR_OMS_PROJECTION','OMS_TERMINAL_PROJECTED'].includes(r.resolution_status as string|null) ||
    (r.resolution_status===null)!==(r.resolution_sha256===null) || !Array.isArray(r.resolution_issues) ||
    r.resolution_issues.length>30 || !r.resolution_issues.every(reason) || new Set(r.resolution_issues).size!==r.resolution_issues.length ||
    r.resolution_status===null && r.resolution_issues.length>0) return false;
  return true;
}
export function residentStatusValid(v:unknown, asOf:number):boolean {
  if (!object(v) || Object.keys(v).length!==3 || !['status','report','last_result'].every(k=>k in v)) return false;
  if (['NO_SNAPSHOT','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status))) return v.report===null && v.last_result===null;
  return v.status==='AVAILABLE' && reportValid(v.report,asOf) && (v.last_result===null || reportValid(v.last_result,asOf));
}
