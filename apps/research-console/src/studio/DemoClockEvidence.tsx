import type {DemoClockReport} from '@/lib/types/demo-clock';
import type {DemoOpeningAssessment} from '@/lib/types/binance-demo';
const clockStamp=(ms:number)=>new Date(ms).toISOString().replace('T',' ').replace('Z',' UTC');

export function DemoClockEvidence({clock,opening}:{clock?:DemoClockReport;opening?:DemoOpeningAssessment|null}) {
  if(!clock)return null;
  return <details className="s-demo-identities"><summary>时间口径与不确定区间</summary>
    <p>成交使用交易所原始时间，接收与可用时间使用本地时钟；两者分别保留。</p>
    <dl>
      <div><dt>交易所时钟 − 本地时钟</dt><dd>{clock.offset_lower_ms} 至 {clock.offset_upper_ms} ms</dd></div>
      <div><dt>两端观察完成</dt><dd>{clockStamp(clock.available_at_ms)}</dd></div>
      {opening?.clock_boundary&&<>
        <div><dt>账户实际观察时间 · 本地时钟</dt><dd>{clockStamp(opening.clock_boundary.local_observed_at_ms)}</dd></div>
        <div><dt>账本保守起算界限 · 交易所时钟</dt><dd>{clockStamp(opening.clock_boundary.venue_not_before_ms)}</dd></div>
      </>}
    </dl>
    <p>区间包含时间戳精度与窗口内 500 ms 漂移假设；该假设尚未独立核验。时间检查只确认区间相容，不提供精确延迟或因果认证。</p>
    <p>这是取得窗口两端观察后形成的回顾记录，不能用于证明当时已知的信息。账本起算界限若存在，是保守计算边界，不是新的账户观察。</p>
  </details>;
}
