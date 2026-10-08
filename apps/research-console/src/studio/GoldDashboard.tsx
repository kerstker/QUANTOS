import {useState} from 'react';
import {GoldTradeReview} from './GoldTradeReview';
import type {GoldObservationPayload} from '@/lib/types/research';
import {Chart,type ChartRow} from './Charts';
import {stamp} from './data';
import './gold-dashboard.css';
const labels:Record<string,string>={CONNECTED:'账户流已连接',CONNECTING:'正在连接账户',DISCONNECTED:'账户流已断开',STOPPED:'采集器已停止',STALE:'数据已过期',DATA_BLOCKED:'数据校验失败',DATA_CONFLICT:'账户事件冲突',EMPTY:'等待账户事件',NOT_CONNECTED:'等待账户数据'};
const number=(value:string|number|undefined|null,digits=2)=>value==null?'—':Number.isFinite(Number(value))?Number(value).toLocaleString('zh-CN',{maximumFractionDigits:digits}):'—';
export function GoldDashboard({data}:{data:GoldObservationPayload|null}) {
 const [side,setSide]=useState('ALL');
 const [dataset,setDataset]=useState('history');
 const history=dataset==='history'?data?.history:null;
 const monitor=data?.monitor;
 const all=[...(history?.trades??monitor?.recent_trades??[])].sort((a,b)=>a.time_ms-b.time_ms);
 const trades=all.filter(t=>side==='ALL'||t.side===side);
 const latest=all[all.length-1];
 const rows:ChartRow[]=trades.map(t=>({label:stamp(t.time_ms,true),time:t.time_ms,[t.side==='BUY'?'buy':'sell']:Number(t.price)}));
 const quantityRows:ChartRow[]=trades.map(t=>({label:stamp(t.time_ms,true),time:t.time_ms,[t.side==='BUY'?'buy':'sell']:Number(t.quantity)}));
 const buy=all.filter(t=>t.side==='BUY').length,sell=all.filter(t=>t.side==='SELL').length;
 const connected=data?.monitor_status==='CONNECTED';
 return <div className="g-dashboard">
  <section className="g-hero" aria-label="黄金账户概览">
   <div className="g-symbol"><span className="g-metal">Au<small>79</small></span><div><span className="g-eyebrow">GOLD / PERPETUAL</span><h2>XAU<span>USDT</span></h2><p>Binance · USDⓈ-M 合约</p></div></div>
   <div className="g-last"><span>最近账户成交价 <small>非市场现价</small></span><strong>{number(latest?.price)}<small> USDT</small></strong><p>{latest?stamp(latest.time_ms,true):'等待第一笔成交记录'}</p></div>
   <div className="g-live"><span className={connected?'g-status is-live':'g-status'}><i/>{labels[data?.monitor_status??'NOT_CONNECTED']??'状态未知'}</span><p>每 15 秒刷新 · 只读观察</p><small>{monitor?'更新于 '+stamp(monitor.observed_at_ms,true):'尚无状态更新时间'}</small></div>
  </section>
  <div className="g-section-title"><div><h3>{history?'历史账户记录':'实时监控记录'}</h3><p>{history?`${stamp(history.start_ms,true)} — ${stamp(history.end_ms,true)} · 查询 ${history.completed_tasks}/${history.total_tasks} 分段 · ${history.status==='QUERIED'?`查询已完成，${history.retention_gap_count??0} 个保留期边界范围未覆盖` :'仍有分段待补录'}`:data?.history_status==='DATA_BLOCKED'?'历史资料校验失败，暂不展示':data?.history_status==='PARTIAL'?'历史导入进行中':'实时监控启动后保存的记录'}</p></div>
   <div className="g-segments" role="group" aria-label="记录来源"><button aria-pressed={!!history} disabled={!data?.history} onClick={()=>setDataset('history')}>历史记录</button><button aria-pressed={!history} onClick={()=>setDataset('live')}>实时监控</button></div></div>
  <div className="g-metrics">
   <article><span>{history?'历史去重成交':'累计记录成交'}</span><strong>{history?.trade_count??monitor?.unique_trades??'—'}<small> 笔</small></strong><p>{history?'所选历史查询范围':'来自已保存的账户日志'}</p></article>
   <article><span>{history?'资金流水':'账户事件'}</span><strong>{history?.income_count??monitor?.event_count??'—'}<small> 条</small></strong><p>{history?'账户全部品种，非黄金净利润':'成交、订单及账户更新'}</p></article>
   <article><span>最近成交方向</span><div className="g-split-value"><b>{buy}</b><small>买入</small><b>{sell}</b><small>卖出</small></div><div className="g-direction-bar" aria-label={`最近记录买入${buy}笔，卖出${sell}笔`}><i style={{width:all.length?`${buy/all.length*100}%`:'0%'}}/><b style={{width:all.length?`${sell/all.length*100}%`:'0%'}}/></div><p>仅统计图中最近 {all.length} 笔</p></article>
   <article><span>保留缺口</span><strong className="g-amber">{monitor?.gap_count??'—'}<small> 段</small></strong><p>{monitor?.recovery_status==='WINDOW_CHECKED'?'最近核对窗口一致':'最近核对：'+(monitor?.recovery_status==='MISMATCH'?'存在差异':'待确认')} · 完整历史未确认</p></article>
  </div>
  {history?.review&&<GoldTradeReview review={history.review}/>}
  {history&&<Chart title="历史每日成交笔数" rows={history.daily.map(d=>({label:new Date(d.time_ms).toISOString().slice(0,10),time:d.time_ms,buy:d.buy_count,sell:d.sell_count}))} series={[{key:'buy',label:'买入笔数'},{key:'sell',label:'卖出笔数',color:'var(--s-danger)'}]} kind="bar" unit="笔" note={`UTC日期 · 全部 ${history.trade_count} 笔历史成交 · 只展示有成交的日期`}/>}
  <div className="g-section-title"><div><h3>成交轨迹</h3><p>{history?`图中展示最近 ${all.length} 笔，全部 ${history.trade_count} 笔已保存。`:'每个点代表一笔账户成交，不连线推算行情。'}</p></div><div className="g-segments" role="group" aria-label="成交方向筛选">{[['ALL','全部方向'],['BUY','仅买入'],['SELL','仅卖出']].map(([value,label])=><button key={value} aria-pressed={side===value} onClick={()=>setSide(value)}>{label}</button>)}</div></div>
  <div className="g-chart-grid">
   <Chart title="成交价格分布" rows={rows} series={[{key:'buy',label:'买入',color:'var(--s-accent)'},{key:'sell',label:'卖出',color:'var(--s-danger)'}]} kind="scatter" xAxis="time" unit="USDT" note="最近账户成交 · 按真实成交时间分布"/>
   <Chart title="每笔成交数量" rows={quantityRows} series={[{key:'buy',label:'买入数量',color:'var(--s-accent)'},{key:'sell',label:'卖出数量',color:'var(--s-danger)'}]} kind="bar" unit="数量" note="按成交先后排列 · 非持仓曲线"/>
  </div>
  <div className="g-bottom-grid">
   <section className="g-card"><div className="g-card-title"><h3>成交时间线</h3><span>最近 {trades.length} 笔</span></div>
    {!trades.length?<div className="g-no-data">暂无符合筛选的成交</div>:<ol className="g-timeline">{[...trades].reverse().map((t,i)=><li key={`${t.time_ms}-${i}`}><span className={t.side==='BUY'?'g-trade-side is-buy':'g-trade-side'}>{t.side==='BUY'?'买':'卖'}</span><div><strong>{number(t.price)} <small>USDT</small></strong><p>{stamp(t.time_ms,true)}</p></div><div className="g-trade-size"><strong>{number(t.quantity,8)}</strong><p>手续费 {t.commission==null?'未知':number(t.commission,8)+' '+(t.commission_asset??'')}</p></div></li>)}</ol>}
   </section>
   <section className="g-card"><div className="g-card-title"><h3>持仓观察</h3><span>最近保存的快照</span></div>
    {!monitor?.positions.length?<div className="g-position-empty"><span>—</span><strong>未提供持仓观察值</strong><p>不据此判定账户空仓</p></div>:monitor.positions.map((p,i)=><div className="g-position" key={i}><span>{p.side==='BOTH'?'单向持仓':p.side==='LONG'?'多头持仓':'空头持仓'}</span><strong>{number(p.quantity,8)}</strong><p>观察均价 {number(p.entry_price)} USDT</p><small>{stamp(p.observed_at_ms,true)}</small></div>)}
    <div className="g-scope-note">已保存成交不等于完整历史；账户净盈亏尚未确认。</div>
   </section>
  </div>
 </div>;
}
