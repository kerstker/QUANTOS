const object=(v:unknown):v is Record<string,unknown> => !!v && typeof v==='object' && !Array.isArray(v);
const exact=(v:Record<string,unknown>,keys:string) => Object.keys(v).sort().join(' ')===keys.split(' ').sort().join(' ');
const integer=(v:unknown):v is number => typeof v==='number' && Number.isSafeInteger(v);
const time=(v:unknown):v is number => integer(v) && v>0 && v<=8640000000000000;
const hash=(v:unknown) => typeof v==='string' && /^[a-f0-9]{64}$/.test(v);
function units(v:unknown):bigint|null {
  if (typeof v!=='string' || !/^\d{1,30}(\.\d{1,36})?$/.test(v)) return null;
  const [whole,fraction='']=v.split('.');
  return BigInt(whole+fraction.padEnd(36,'0'));
}
export function issuerStatusValid(v:unknown,asOf:number):boolean {
  if (!object(v) || !exact(v,'status report')) return false;
  if (['NO_SNAPSHOT','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status))) return v.report===null;
  const r=v.report;
  if (!['AVAILABLE','STALE'].includes(String(v.status)) || !object(r) ||
    !exact(r,'schema environment scope phase observed_at_ms source_commit account_ref_sha256 policy_id policy_sha256 symbol valid_from_ms valid_until_ms key_status key_material_status records last_record_at_ms journal_sha256 budgets source_verified submission_enabled cash_reconciled economic_authority') ||
    r.schema!=='DEMO_ISSUER_STATUS_V1' || r.environment!=='BINANCE_USDM_DEMO' || r.scope!=='TECHNICAL_VALIDATION_ONLY' ||
    !['source_verified','submission_enabled','cash_reconciled','economic_authority'].every(k=>r[k]===false) ||
    !time(r.observed_at_ms) || r.observed_at_ms>asOf || (v.status==='STALE')!==(asOf-r.observed_at_ms>120000) ||
    typeof r.source_commit!=='string' || !/^[a-f0-9]{40}$/.test(r.source_commit) ||
    !hash(r.account_ref_sha256) || !hash(r.policy_sha256) || typeof r.policy_id!=='string' || !/^[A-Za-z0-9_.:-]{1,128}$/.test(r.policy_id) ||
    !['BTCUSDC','BTCUSDT'].includes(String(r.symbol)) || !['ACTIVE','REVOKED','MISSING'].includes(String(r.key_status)) || r.key_material_status!=='NOT_CHECKED' ||
    !time(r.valid_from_ms) || !time(r.valid_until_ms) || r.valid_from_ms>=r.valid_until_ms ||
    !integer(r.records) || r.records<0 || r.records>20000 ||
    (r.records>0 ? !hash(r.journal_sha256) || !time(r.last_record_at_ms) || r.last_record_at_ms>r.observed_at_ms : r.journal_sha256!==null || r.last_record_at_ms!==null) ||
    !Array.isArray(r.budgets) || r.budgets.length!==2) return false;
  let records=0,limited=false,pending=false;
  if (!r.budgets.every((b:unknown,i:number)=>{
    if (!object(b) || !exact(b,'purpose reserved_grants signed_grants reserved_orders max_grants max_orders reserved_notional max_notional remaining_notional') ||
      b.purpose!==['submission','cancellation'][i] || !integer(b.reserved_grants) || !integer(b.signed_grants) ||
      !integer(b.reserved_orders) || !integer(b.max_grants) || !integer(b.max_orders) ||
      b.signed_grants<0 || b.signed_grants>b.reserved_grants || b.reserved_grants>b.max_grants || b.max_grants<1 || b.max_grants>4096 ||
      b.reserved_orders<b.reserved_grants || b.reserved_orders>Math.min(b.max_orders,b.reserved_grants*20) || b.max_orders<1 || b.max_orders>50000) return false;
    records+=b.reserved_grants+b.signed_grants;
    pending ||= b.reserved_grants>b.signed_grants;
    limited ||= b.reserved_grants===b.max_grants || b.reserved_orders===b.max_orders;
    if (i===1) return b.reserved_notional===null && b.max_notional===null && b.remaining_notional===null;
    const used=units(b.reserved_notional),limit=units(b.max_notional),left=units(b.remaining_notional);
    if (used===null || limit===null || left===null || limit<=0n || used+left!==limit || (used>0n)!==(b.reserved_orders>0)) return false;
    limited ||= left===0n;return true;
  }) || records>r.records) return false;
  const inactive=r.key_status!=='ACTIVE' || r.observed_at_ms<r.valid_from_ms || r.observed_at_ms>=r.valid_until_ms;
  return r.phase===(inactive?'POLICY_INACTIVE':limited?'LIMIT_REACHED':pending?'PENDING_SIGNATURE':'POLICY_OBSERVED');
}
