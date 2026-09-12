import {clockReportValid} from './demo-clock-contract';
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const hash = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
const decimal = (v: unknown): v is string => typeof v === 'string' && /^-?\d{1,35}(\.\d{1,24})?$/.test(v);
const amount = (v: unknown) => v===null || decimal(v);
const codeList = (v: unknown): v is string[] => Array.isArray(v) && v.length<=100 &&
  v.every(x => typeof x==='string' && /^[A-Z0-9_]{1,100}$/.test(x)) && new Set(v).size===v.length;
const zero = (v: unknown) => decimal(v) && /^-?0+(\.0+)?$/.test(v);

export function canonicalCashValid(v: unknown, asOf: number): boolean {
  if (!object(v)) return false;
  if (['INPUTS_MISSING','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status))) return v.report===null;
  const r=v.report;
  if (object(r)&&r.clock!==undefined&&(!clockReportValid(r.clock,asOf)||
      !Number.isSafeInteger(r.observed_at_ms)||Number(r.observed_at_ms)<r.clock.available_at_ms)) return false;
  if (v.status!=='AVAILABLE' || !object(r) || r.schema!=='DEMO_CANONICAL_CASH_COMPARISON_V1' ||
      r.environment!=='BINANCE_USDM_DEMO' || r.scope!=='TECHNICAL_VALIDATION_ONLY' ||
      !['MATCHED_REPORTED_INPUTS','DIFFERENCE_FOUND','UNRESOLVED'].includes(String(r.status)) ||
      !['USDT','USDC'].includes(String(r.quote_asset)) || r.symbol!==`BTC${r.quote_asset}` ||
      !['account_ref_sha256','manifest_hash','journal_tail','cash_capture_sha256','replay_source_sha256','projection_source_sha256'].every(k=>hash(r[k])) ||
      !['MATCHED_REPORTED_WINDOW','DIFFERENCE_FOUND','UNRESOLVED'].includes(String(r.cash_input_status)) ||
      !Number.isSafeInteger(r.cash_input_duplicates) || Number(r.cash_input_duplicates)<0 || Number(r.cash_input_duplicates)>10000 ||
      !Number.isSafeInteger(r.observed_at_ms) || Number(r.observed_at_ms)<=0 || Number(r.observed_at_ms)>asOf ||
      !codeList(r.issues) || !codeList(r.limitations) || !r.limitations.length ||
      !['canonical_wallet','reported_wallet','wallet_residual'].every(k=>amount(r[k])) ||
      !['UNKNOWN','MATCHED_REPORTED_TRADES','NO_REPORTED_TRADES'].includes(String(r.fee_comparison)) ||
      !['UNKNOWN','MATCHED_REPORTED_TOTAL','DIFFERENCE_FOUND'].includes(String(r.realized_pnl_comparison)) ||
      r.source_verified!==false || r.coverage_verified!==false || r.authoritative!==false || r.submission_enabled!==false || r.strategy_pnl!==null ||
      !(r.ledger_state_hash===null || hash(r.ledger_state_hash))) return false;
  const c=r.reconciliation;
  if (c!==null && (!object(c) || c.reported_cash_policy!=='REPORTED_QUOTE_CASH_AFTER_FILLS_V1' ||
      !['initial_equity','ending_equity','price_pnl','fee','funding','external_cash_flow','trading_and_funding_change','residual'].every(k=>decimal(c[k])))) return false;
  if (r.status==='MATCHED_REPORTED_INPUTS') return r.issues.length===0 && zero(r.wallet_residual) && object(c) && zero(c.residual) &&
    decimal(r.canonical_wallet) && decimal(r.reported_wallet) && r.canonical_wallet===r.reported_wallet && hash(r.ledger_state_hash) &&
    r.cash_input_status!=='UNRESOLVED' && r.fee_comparison!=='UNKNOWN' && r.realized_pnl_comparison==='MATCHED_REPORTED_TOTAL';
  if (r.status==='DIFFERENCE_FOUND') return r.issues.length>0 && object(c) && decimal(r.wallet_residual) && hash(r.ledger_state_hash);
  return r.issues.length>0;
}
