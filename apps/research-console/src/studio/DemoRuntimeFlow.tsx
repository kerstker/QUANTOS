import {useEffect,useState} from 'react';
import type {BinanceDemoPayload} from '@/lib/types/binance-demo';
import {stamp} from './data';
import './demo-runtime-flow.css';
type State='ready'|'waiting'|'blocked'|'stale'|'missing';
type Node={id:string;title:string;state:State;detail:string;time:number|null;href:string;value:string};
const labels:Record<State,string>={ready:'有效记录',waiting:'等待推进',blocked:'需要处理',stale:'时效未知',missing:'尚无证据'};
export function runtimeNodes(d:BinanceDemoPayload|null,now:number,failed:boolean):Node[] {
  const fresh=(t:number|null|undefined)=>!failed && typeof t==='number' && Number.isFinite(t) && t<=now && now-t<=120000;
  function observed(id:string,title:string,status:string|undefined,t:number|null|undefined,ok:boolean,detail:string,href:string,value:string):Node {
    const missing=!status || status==='NO_SNAPSHOT';
    const damaged=status==='CORRUPT_OR_UNREADABLE'||status==='DATA_BLOCKED';
    return {id,title,state:damaged?'blocked':missing?'missing':status==='STALE'||!fresh(t)?'stale':ok?'ready':'blocked',detail,time:t??null,href,value};
  }
  const market=d?.market_inputs?.BTCUSDC,origin=d?.execution_origin,cycle=d?.portfolio_cycle,cash=d?.portfolio_cash,backup=d?.backup_status;
  const r=cycle?.report;
  const cycleFresh=cycle?.status==='AVAILABLE' && fresh(r?.observed_at_ms);
  const portfolio=observed('decision','组合决策',cycle?.status,r?.observed_at_ms,!!r && !['INPUTS_UNRESOLVED','CONTROLLER_RESULT_NOT_RETAINED','EXECUTION_NOT_COMPLETED'].includes(r.status),r?.status??'尚无组合周期记录，不能从历史固定测试推断组合已启动。','#demo-cycle',r?.capital?`${r.capital} USDC`:'待运行');
  const execution:Node={id:'execution',title:'执行与恢复',state:cycleFresh&&r?(r.fill_count&&r.fill_count>0?'ready':r.status==='TASK_READY'||r.status==='NO_CHANGE'?'waiting':'blocked'):portfolio.state==='missing'?'missing':'stale',detail:cycleFresh&&r?`${r.status} · ${r.fill_count??0} 笔已核对成交；任务准备不等于订单成交。`:'本轮无有效组合成交证据；旧 OMS 记录不计入本轮。',time:r?.observed_at_ms??null,href:'#demo-orders',value:cycleFresh&&r?`${r.fill_count??0} 笔成交`:'待成交'};
  const linked=!!r?.cash_report_sha256 && cash?.status==='AVAILABLE' && cash.report?.report_sha256===r.cash_report_sha256 && fresh(cash.report?.observed_at_ms);
  const settled=cycleFresh&&r?.cash_reconciled===true&&r.status==='CASH_RECONCILED'&&linked;
  const cashNode:Node={id:'cash',title:'现金核对',state:settled?'ready':cycleFresh&&r?.cash_reconciled?'blocked':portfolio.state==='missing'?'missing':cycleFresh?'waiting':'stale',detail:settled?'本轮组合现金报告与周期摘要匹配。':r?.cash_reconciled?'现金明细缺失、过期或与本轮不匹配。':'等待本轮成交、费用、资金流水和钱包核对。',time:cash?.report?.observed_at_ms??null,href:'#demo-cash',value:settled?'本轮匹配':'未闭合'};
  return [
    observed('market','行情输入',market?.status,market?.report?.observed_at_ms,market?.report?.capture_status==='CAPTURED',market?.report?.failure?.reason??'BTCUSDC Demo执行行情；不是组合公开研究行情的健康证明。','#demo-diagnostics',market?.report?.metrics?`${market.report.metrics.bars.length} 根分钟线`:'待采集'),
    {id:'research',title:'预测观察',state:'missing',detail:'组合预测由独立观察器保存；当前接口未发布该快照。此处未知不代表观察器停止。',time:null,href:'/studio/factory',value:'待接入视图'},
    observed('account','账户核验',origin?.status,origin?.report?.observed_at_ms,origin?.report?.status==='OBSERVED_FLAT_QUOTE',origin?.report?.issues.join(' · ')||'等待当前持仓、行情时钟及账户起点校验。','#demo-diagnostics',origin?.report?.wallet?`${origin.report.wallet} USDC`:'待核验'),
    portfolio,execution,cashNode,
    observed('backup','恢复保障',backup?.status,backup?.report?.observed_at_ms,backup?.report?.phase==='ALL_RECOVERY_POINTS_CURRENT','原四类命名空间恢复点；不代表专用组合观察备份已接入本视图。','#demo-diagnostics',backup?.report?`${backup.report.components.filter(c=>c.summary?.status==='CURRENT').length} / ${backup.report.components.length} 当前恢复点`:'待同步'),
  ];
}
export function DemoRuntimeFlow({data,refreshFailed}:{data:BinanceDemoPayload|null;refreshFailed:boolean}) {
  const [now,setNow]=useState(()=>Date.now());
  const [selected,setSelected]=useState('account');const [paused,setPaused]=useState(false);
  useEffect(()=>{const timer=window.setInterval(()=>setNow(Date.now()),1000);return ()=>window.clearInterval(timer);},[]);
  const nodes=runtimeNodes(data,now,refreshFailed),node=nodes.find(n=>n.id===selected)??nodes[0];
  const blocked=nodes.filter(n=>n.state==='blocked');
  const stale=nodes.filter(n=>n.state==='stale');
  const complete=nodes.find(n=>n.id==='cash')?.state==='ready';
  const title=refreshFailed?'快照更新中断':blocked.length?'运行链路存在阻塞':stale.length?'部分数据已过期':complete?'本轮组合现金已核对':'观察与执行尚未闭合';
  return <section className="s-runtime" aria-label="实时运行链路" data-paused={paused}>
    <header className="s-runtime-heading"><div><p className="s-eyebrow">OPERATIONS / EVIDENCE FLOW</p><h2>{title}</h2><p>Binance Demo · BTCUSDC · 当前组合周期</p></div><button type="button" className="s-button" aria-pressed={paused} onClick={()=>setPaused(!paused)}>{paused?'恢复动画':'暂停动画'}</button></header>
    <div className="s-runtime-summary" role="status">{refreshFailed?'读取失败，停止显示数据流动。':blocked.length?`需要处理：${blocked.map(n=>n.title).join('、')}`:stale.length?`需要刷新：${stale.map(n=>n.title).join('、')}`:'点击任一节点，查看它的真实记录与下一步。'}</div>
    <ol className="s-runtime-track" aria-label="运行阶段">
      {nodes.map((n,index)=><li key={n.id} className={`s-runtime-node ${n.state}`}>
        <button type="button" aria-pressed={selected===n.id} onClick={()=>setSelected(n.id)}>
          <span className="s-runtime-step">{String(index+1).padStart(2,'0')}<span>{labels[n.state]}</span></span>
          <strong>{n.title}</strong><span className="s-runtime-value">{n.value}</span>
          <small>{n.time?stamp(n.time,true):'尚无时间戳'}</small>
          {n.state==='ready'&&<span key={`${n.id}-${n.time}`} className="s-runtime-receipt" aria-hidden="true"/>}
        </button>
      </li>)}
    </ol>
    <div className={`s-runtime-detail ${node.state}`} aria-live="polite"><div><span>{labels[node.state]} · {node.title}</span><p>{node.detail}</p></div><a className="s-button" href={node.href}>查看证据 →</a></div>
    <footer><span>动画只在有效记录更新时触发；不表示下单或吞吐量。</span><span>15秒读取快照 · 过期自动停流</span></footer>
  </section>;
}
