import {useState} from 'react';
import type {ForwardCostAccount} from '@/lib/types/ai-factory';
import {Panel} from './components';
import {Chart} from './Charts';
import {fmt,stamp} from './data';

export function ForwardCostEvidence({data,title='前向成本账户'}:{data?:ForwardCostAccount|null;title?:string}) {
 const [scenario,setScenario]=useState('4/5');
 const evidence=data?.cases?data:data?.last_completed;
 const selected=evidence?.cases?.filter(c=>`${c.fee_bps}/${c.slippage_bps}`===scenario)??[];
 const model=selected.find(c=>c.model==='FROZEN_MODEL'),cash=selected.find(c=>c.model==='CASH'),baseline=selected.find(c=>c.model==='SINGLE_BASELINE');
 const baselineByTime=new Map(baseline?.equity.map(r=>[r.time,Number(r.equity)]));
 const rows=model?.equity.map((r,i)=>({label:stamp(r.time,true),time:r.time,model:Number(r.equity),baseline:baselineByTime.get(r.time),cash:cash?.equity[i]?.time===r.time?Number(cash.equity[i].equity):undefined}))??[];
 const status:Record<string,string>={WAITING_MATURITY:'未来窗口已登记，等待样本成熟',MISSING_FORECASTS:'存在预测断档，暂停账户回放',ERROR:'本轮更新失败，已有回执保留',RETRY_BACKOFF:'等待一小时后重试',RETRY_BUDGET_EXHAUSTED:'本窗口三次尝试已用完，停止自动重试',FORWARD_FORECAST_ACCOUNT_TECHNICAL_ONLY:'前向预测账户已完成技术回放'};
 return <>
 <Panel title={title} note="提前冻结十四天窗口与持仓规则；逐日延长同一个账户，不每日重置本金。随前向观察更新。">
 <p role="status">{data?(status[data.status]??data.status):'尚未登记；运行盈利验证后登记下一自然日的窗口。'}</p>
 {data?.window_start_ms!==undefined&&<p>窗口 {stamp(data.window_start_ms,true)} — {stamp(data.window_end_ms,true)} · 预测缺口 {data.missing_slots?.length??'未知'}</p>}
 {data?.last_completed&&<p>本次窗口尚未完成，以下保留上次已完成窗口的结果。</p>}
 {evidence?.window&&<p>已成熟 {evidence.window.mature_days} / {evidence.window.planned_days} 天 · {stamp(evidence.window.window_start_ms,true)} — {stamp(evidence.window.window_end_ms,true)}</p>}
 {evidence?.cases&&<div className="s-table-wrap" role="region" aria-label="前向账户成本对照" tabIndex={0}><table className="s-table"><thead><tr><th>账户</th><th>费用 / 滑点 bp</th><th>毛收益</th><th>净收益 USDC</th><th>手续费</th><th>资金费</th><th>对账残差</th></tr></thead><tbody>{evidence.cases.map(c=><tr key={`${c.model}/${c.fee_bps}/${c.slippage_bps}`}><td>{c.model==='CASH'?'空仓基线':c.model==='SINGLE_BASELINE'?'内层单模型基线':'冻结预测'}</td><td>{c.fee_bps} / {c.slippage_bps}</td><td>{c.attribution.gross_reference_pnl===null?'未知':fmt(Number(c.attribution.gross_reference_pnl))}</td><td title={c.attribution.net_pnl??undefined}>{c.attribution.net_pnl==null?'未知':fmt(Number(c.attribution.net_pnl),4)}</td><td title={c.attribution.fees??undefined}>{c.attribution.fees==null?'未知':fmt(Number(c.attribution.fees),4)}</td><td title={c.attribution.funding??undefined}>{c.attribution.funding==null?'未知':fmt(Number(c.attribution.funding),4)}</td><td title={c.attribution.reconciliation_residual??undefined}>{c.attribution.reconciliation_residual==null?'未知':fmt(Number(c.attribution.reconciliation_residual),4)}</td></tr>)}</tbody></table></div>}
 <p className="f-message">这是提前留存预测的模拟账户结果；真实场所费用与成交校准仍未知。未下单，不构成经济准入或稳定盈利证明。</p>
 </Panel>
 {rows.length>0&&<><label>前向账户成本情景 <select value={scenario} onChange={e=>setScenario(e.target.value)}><option value="4/5">费用 4 / 滑点 5 bp</option><option value="4/10">费用 4 / 滑点 10 bp</option><option value="8/5">费用 8 / 滑点 5 bp</option></select></label><Chart title="前向模拟账户 · 同窗口权益" rows={rows} series={[{key:'model',label:'冻结预测'},{key:'cash',label:'空仓基线'},...(baseline?[{key:'baseline',label:'内层单模型基线'}]:[])]} unit="USDC"/></>}
 </>;
}
