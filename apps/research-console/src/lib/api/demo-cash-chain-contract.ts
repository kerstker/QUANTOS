const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const int=(v:unknown):v is number=>typeof v==='number'&&Number.isSafeInteger(v);
const decimal=(v:unknown):v is string=>typeof v==='string'&&/^-?\d{1,35}(\.\d{1,24})?$/.test(v);
const amounts=['initial_equity','canonical_wallet','reported_wallet','wallet_residual','quantity','entry_notional','fee','funding','external_cash_flow'];
const hashes=['report_sha256','cash_capture_sha256','replay_source_sha256','projection_source_sha256'];
const flags=['source_verified','coverage_verified','authoritative','submission_enabled','cash_reconciled'];
function scaled(v:string):bigint {const [whole,fraction='']=v.split('.');return BigInt(whole+fraction.padEnd(24,'0'));}

export function cashChainValid(v:unknown,asOf:number):boolean {
  if(!object(v))return false;
  if(['NO_SNAPSHOT','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status)))return v.report===null;
  const r=v.report;
  const fields=['schema','environment','scope','phase','observed_at_ms','account_ref_sha256','symbol','quote_asset','batch_count',
    'continuations_matched','issues','ledger_state_hash','strategy_pnl','batches',...amounts,...hashes,...flags];
  if(v.status!=='AVAILABLE'||!object(r)||Object.keys(r).length!==fields.length||!fields.every(k=>k in r)||
    r.schema!=='DEMO_CASH_CHAIN_STATUS_V1'||r.environment!=='BINANCE_USDM_DEMO'||r.scope!=='TECHNICAL_VALIDATION_ONLY'||
    !['UNRESOLVED','MATCHED_REPORTED_INPUTS','DIFFERENCE_FOUND'].includes(String(r.phase))||
    flags.some(k=>r[k]!==false)||r.strategy_pnl!==null||!int(r.observed_at_ms)||r.observed_at_ms<=0||r.observed_at_ms>asOf||
    hashes.some(k=>!hash(r[k]))||['ledger_state_hash','account_ref_sha256'].some(k=>r[k]!==null&&!hash(r[k]))||
    !(r.symbol===null&&r.quote_asset===null)&&(!['USDC','USDT'].includes(String(r.quote_asset))||r.symbol!==`BTC${r.quote_asset}`)||
    amounts.some(k=>r[k]!==null&&!decimal(r[k]))||!int(r.batch_count)||r.batch_count<0||r.batch_count>32||
    !int(r.continuations_matched)||r.continuations_matched<0||r.continuations_matched>Math.max(0,r.batch_count-1)||
    !Array.isArray(r.issues)||r.issues.length>100||new Set(r.issues).size!==r.issues.length||
    r.issues.some(i=>typeof i!=='string'||!/^[A-Z][A-Z0-9_]{0,99}$/.test(i))||
    !Array.isArray(r.batches)||r.batches.length!==r.batch_count)return false;
  let previous=-1;
  for(const b of r.batches){
    if(!object(b)||Object.keys(b).length!==6||!hash(b.manifest_hash)||!hash(b.journal_tail)||
      !int(b.orders_count)||b.orders_count<1||b.orders_count>200||!int(b.trades_count)||b.trades_count<0||b.trades_count>10000||
      !int(b.start_ms)||!int(b.end_ms)||b.start_ms<0||b.end_ms-b.start_ms<2||b.end_ms-b.start_ms>150000||b.end_ms>r.observed_at_ms||
      r.phase!=='UNRESOLVED'&&b.start_ms<=previous)return false;
    previous=b.start_ms;
  }
  if(r.phase==='UNRESOLVED')return r.issues.length>0&&r.ledger_state_hash===null&&amounts.filter(k=>k!=='reported_wallet').every(k=>r[k]===null);
  if(!r.batch_count||r.continuations_matched!==r.batch_count-1||amounts.some(k=>!decimal(r[k]))||!hash(r.ledger_state_hash)||
    !hash(r.account_ref_sha256)||r.symbol===null||scaled(r.entry_notional as string)<0n||scaled(r.fee as string)<0n)return false;
  if(r.phase==='MATCHED_REPORTED_INPUTS')return r.issues.length===0&&scaled(r.wallet_residual as string)===0n&&
    scaled(r.canonical_wallet as string)===scaled(r.reported_wallet as string);
  return r.issues.length>0;
}
