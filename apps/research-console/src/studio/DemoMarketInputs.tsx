import {useState} from 'react';
import type {DemoMarketInputsSnapshots,DemoMarketSymbol} from '@/lib/types/demo-market-inputs';
import {Chart} from './Charts';
import {Empty,Metric,Panel} from './components';
import {stamp} from './data';

const reasons={REQUEST_FAILED:'行情请求失败',RATE_LIMITED:'场所请求限流',RESPONSE_INVALID:'响应格式异常',
  INPUTS_INVALID:'行情窗口未通过检查',CAPTURE_BUDGET_EXHAUSTED:'行情采集超时'};
export function DemoMarketInputs({snapshots,refreshFailed=false}:{snapshots?:DemoMarketInputsSnapshots;refreshFailed?:boolean}) {
  const [symbol,setSymbol]=useState<DemoMarketSymbol>('BTCUSDC');
  const snapshot=snapshots?.[symbol],r=snapshot?.report,m=r?.metrics;
  const historical=refreshFailed||snapshot?.status==='STALE';
  return <section aria-label="Demo 行情与风险输入">
    <Panel title="行情与风险输入" note="Demo 场所 · 两种计价分别采集" action={<div className="s-chart-actions">
      {(['BTCUSDC','BTCUSDT'] as const).map(s=><button key={s} className="s-text-link" aria-pressed={symbol===s} onClick={()=>setSymbol(s)}>{s}</button>)}
    </div>}>
      {!r?<Empty title={snapshot?.status==='DATA_BLOCKED'?'行情时间或币种异常':snapshot?.status==='CORRUPT_OR_UNREADABLE'?'行情记录无法读取':`尚无 ${symbol} 行情输入`}>
        等待这一合约的成交量与已收盘 K 线。缺失数据不会用另一合约替代。
      </Empty>:<div className="s-demo-replay">
        {historical&&<p className="s-demo-notice" role="status">{refreshFailed?'刷新失败，保留历史行情。':'行情已过期，以下为历史测量，不能用于当前请求。'}</p>}
        <p className="s-demo-description">{symbol} · 采集完成 {stamp(r.completed_at_ms,true)} · 用时 {(r.completed_at_ms-r.observed_at_ms)/1000} 秒</p>
        {m?<><div className="s-metrics s-demo-metrics s-demo-issuer-metrics">
          <Metric label="24 小时成交量" value={`${m.daily_volume_base} BTC`} note="场所滚动窗口；使用基础资产数量"/>
          <Metric label="一小时已实现波动率" value={`${(Number(m.instrument_vol)*100).toFixed(6)}%`} note="60 个分钟对数收益平方和的平方根；未年化"/>
          <Metric label="已收盘样本" value="61 根" note={`最后收盘 ${stamp(m.last_close_ms,true)}`}/>
        </div><p className="s-demo-description">波动率描述已经发生的价格变化。Demo 成交量不能代表主网可成交容量。</p></>
        :<Empty title={r.failure?reasons[r.failure.reason]:'行情输入未知'}>本次失败已保留，没有可用于风控的测量值。</Empty>}
        <details className="s-demo-identities"><summary>查看测量方法与来源</summary>
          <p>只读取已收盘的连续分钟 K 线；请求生成要求测量完成后不超过 30 秒。原始响应保留在采集记录中。</p>
          <p>输入摘要：<code>{r.input_sha256}</code></p><p>采集源码摘要：<code>{r.source_sha256}</code></p>
        </details>
      </div>}
    </Panel>
    {m&&<Chart key={symbol} title={`${symbol} 已收盘分钟 K 线`} kind="candle" unit={symbol.slice(3)} xAxis="time"
      note={`${historical?'历史记录 · ':''}61 根已收盘 K 线 · 支持逐点查看与导出`}
      rows={m.bars.map(b=>({time:b.close_ms,label:stamp(b.close_ms,true),open:Number(b.open),high:Number(b.high),low:Number(b.low),close:Number(b.close)}))}
      series={['open','high','low','close'].map((key,i)=>({key,label:['开盘','最高','最低','收盘'][i]}))}/>}
  </section>;
}
