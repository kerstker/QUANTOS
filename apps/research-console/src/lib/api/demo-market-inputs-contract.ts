const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const exact=(v:Record<string,unknown>,keys:string)=>Object.keys(v).sort().join(' ')===keys.split(' ').sort().join(' ');
const time=(v:unknown):v is number=>typeof v==='number'&&Number.isSafeInteger(v)&&v>0&&v<=8640000000000000;
const hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
function decimal(v:unknown):bigint|null {
  if(typeof v!=='string'||!/^\d{1,30}(\.\d{1,24})?$/.test(v))return null;
  const [whole,fraction='']=v.split('.');const n=BigInt(whole+fraction.padEnd(24,'0'));
  return n<=10n**39n?n:null;
}
function snapshot(v:unknown,symbol:string,asOf:number):boolean {
  if(!object(v)||!exact(v,'status report'))return false;
  if(['NO_SNAPSHOT','DATA_BLOCKED','CORRUPT_OR_UNREADABLE'].includes(String(v.status)))return v.report===null;
  const r=v.report;
  if(!['AVAILABLE','STALE'].includes(String(v.status))||!object(r)||
    !exact(r,'schema environment symbol capture_status observed_at_ms completed_at_ms source_sha256 input_sha256 failure metrics source_verified submission_enabled economic_authority')||
    r.schema!=='DEMO_MARKET_INPUTS_VIEW_V1'||r.environment!=='BINANCE_USDM_DEMO'||r.symbol!==symbol||
    !['source_verified','submission_enabled','economic_authority'].every(k=>r[k]===false)||!hash(r.source_sha256)||!hash(r.input_sha256)||
    !time(r.observed_at_ms)||!time(r.completed_at_ms)||r.observed_at_ms>r.completed_at_ms||r.completed_at_ms>asOf||
    (v.status==='STALE')!==(asOf-r.completed_at_ms>30000))return false;
  if(r.capture_status==='FAILED')return r.metrics===null&&object(r.failure)&&exact(r.failure,'kind reason')&&
    ['clock_before','ticker','klines','clock_after','validation','worker'].includes(String(r.failure.kind))&&
    ['REQUEST_FAILED','RATE_LIMITED','RESPONSE_INVALID','INPUTS_INVALID','CAPTURE_BUDGET_EXHAUSTED'].includes(String(r.failure.reason));
  const m=r.metrics;
  if(r.capture_status!=='CAPTURED'||r.failure!==null||r.completed_at_ms-r.observed_at_ms>40000||!object(m)||
    !exact(m,'daily_volume_base instrument_vol volatility_policy last_close_ms ticker_close_ms return_intervals bars')||
    m.volatility_policy!=='SQRT_SUM_LOG_RETURNS_SQUARED_60X1M_CEIL18_V1'||m.return_intervals!==60||
    decimal(m.daily_volume_base)===null||decimal(m.daily_volume_base)!<=0n||decimal(m.instrument_vol)===null||
    !time(m.last_close_ms)||(m.last_close_ms+1)%60000!==0||m.last_close_ms>r.completed_at_ms+5001||
    !time(m.ticker_close_ms)||m.ticker_close_ms>r.completed_at_ms+5001||m.ticker_close_ms<r.observed_at_ms-35001||
    !Array.isArray(m.bars)||m.bars.length!==61)return false;
  const last=m.last_close_ms;
  return m.bars.every((b,i)=>{
    if(!object(b)||!exact(b,'close_ms open high low close volume_base')||b.close_ms!==last-(60-i)*60000)return false;
    const o=decimal(b.open),h=decimal(b.high),l=decimal(b.low),c=decimal(b.close),volume=decimal(b.volume_base);
    return o!==null&&h!==null&&l!==null&&c!==null&&volume!==null&&l>0n&&l<=o&&l<=c&&h>=o&&h>=c;
  });
}
export function marketInputsValid(v:unknown,asOf:number):boolean {
  return object(v)&&exact(v,'BTCUSDC BTCUSDT')&&['BTCUSDC','BTCUSDT'].every(symbol=>snapshot(v[symbol],symbol,asOf));
}
