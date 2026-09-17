import {portfolioStatuses} from '@/lib/types/demo-portfolio-status';
const object=(v:unknown):v is Record<string,unknown>=>!!v && typeof v==='object' && !Array.isArray(v);
const integer=(v:unknown):v is number=>typeof v==='number' && Number.isSafeInteger(v);
const time=(v:unknown):v is number=>integer(v) && v>0 && v<=8640000000000000;
const hash=(v:unknown)=>typeof v==='string' && /^[a-f0-9]{64}$/.test(v);
const decimal=(v:unknown)=>typeof v==='string' && /^-?\d{1,20}(\.\d{1,18})?$/.test(v);
const fields=('schema environment scope source_commit observed_at_ms status phase allocation_sha256 model_hash capital '+
  'policy_valid_until_ms task_sha256 close_index final_quantity fill_count cash_status cash_report_sha256 '+
  'wallet_residual cash_reconciled economic_authority submission_enabled').split(' ').sort().join(' ');
export function portfolioStatusValid(v:unknown,asOf:number):boolean {
  if (!object(v) || Object.keys(v).sort().join(' ')!=='report status') return false;
  if (['NO_SNAPSHOT','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status))) return v.report===null;
  const r=v.report;
  if (!['AVAILABLE','STALE'].includes(String(v.status)) || !object(r) || Object.keys(r).sort().join(' ')!==fields ||
    r.schema!=='DEMO_PORTFOLIO_STATUS_V1' || r.environment!=='BINANCE_USDM_DEMO' || r.scope!=='TECHNICAL_VALIDATION_ONLY' ||
    !portfolioStatuses.some(s=>s===r.status) || r.economic_authority!==false || r.submission_enabled!==false ||
    !time(r.observed_at_ms) || r.observed_at_ms>asOf || (v.status==='STALE')!==(asOf-r.observed_at_ms>120000) ||
    typeof r.source_commit!=='string' || !/^[a-f0-9]{40}$/.test(r.source_commit) ||
    typeof r.phase!=='string' || !/^[A-Z_]{1,64}$/.test(r.phase) ||
    !['allocation_sha256','model_hash','task_sha256','cash_report_sha256'].every(k=>r[k]===null || hash(r[k])) ||
    !(r.policy_valid_until_ms===null || time(r.policy_valid_until_ms)) ||
    !['capital','final_quantity','wallet_residual'].every(k=>r[k]===null || decimal(r[k])) ||
    !(r.capital===null || Number(r.capital)>0) ||
    !(r.close_index===null || integer(r.close_index) && r.close_index>=1 && r.close_index<=2) ||
    !(r.fill_count===null || integer(r.fill_count) && r.fill_count>=0 && r.fill_count<=10000) ||
    ![null,'MATCHED_REPORTED_INPUTS'].includes(r.cash_status as string|null)) return false;
  const done=r.status==='CASH_RECONCILED';
  if (r.cash_reconciled!==done) return false;
  return !done || ['allocation_sha256','model_hash','capital','policy_valid_until_ms','cash_report_sha256'].every(k=>r[k]!==null) &&
    integer(r.fill_count) && r.fill_count>=2 && r.cash_status==='MATCHED_REPORTED_INPUTS' && r.final_quantity==='0' && r.wallet_residual==='0';
}
