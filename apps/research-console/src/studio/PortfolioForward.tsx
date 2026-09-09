import type {PortfolioResearch} from '@/lib/types/ai-factory';
import {Panel} from './components';
import {ForwardCostEvidence} from './ForwardCostEvidence';
import {fmt,stamp} from './data';

export function PortfolioForward({data,busy,action}:{data?:PortfolioResearch|null;busy:boolean;action:(kind:string)=>void}) {
 const forward=data?.forward,ready=data?.status==='COMPLETED_TECHNICAL_ONLY';
 return <>
 <Panel title="冻结组合 · 配对前向观察" note="组合与内层单模型基线使用同一时点输入；预测提前追加，持仓由冻结的成本与风险规则决定。" action={<div className="f-toolbar"><button className="s-button" disabled={busy||!ready} onClick={()=>action('portfolio_observe')}>启用组合观察</button><button className="s-button" disabled={busy||!forward} onClick={()=>action('portfolio_refresh')}>推进组合观察</button><button className="s-button" disabled={busy||!forward} onClick={()=>action('portfolio_stop')}>停止组合观察</button></div>}>
 <p role="status">{!forward?'尚未启用组合观察':forward.status==='ERROR'?`更新失败 · ${forward.reason??'原因未记录'}`:forward.status==='DISABLED'?'观察已停止，已有预测保留':`已发布 ${forward.predictions??'未知'} 对预测 · 成熟标签 ${forward.labels??'未知'}`}</p>
 {forward&&<p>激活 {stamp(forward.activated_at_ms,true)} · 最近更新 {stamp(forward.observed_at_ms,true)}</p>}
 <div className="s-table-wrap" role="region" aria-label="组合配对预测" tabIndex={0}><table className="s-table"><thead><tr><th>信息时点</th><th>提前发布时间</th><th>组合预测 bp</th><th>单模型预测 bp</th></tr></thead><tbody>{forward?.recent_forecasts?.slice(-12).reverse().map(r=><tr key={r.slot_ms}><td>{stamp(r.slot_ms,true)}</td><td>{stamp(r.issued_at_ms,true)}</td><td>{fmt(r.prediction*10000,3)}</td><td>{r.baseline_prediction==null?'未知':fmt(r.baseline_prediction*10000,3)}</td></tr>)}</tbody></table></div>
 <p className="f-message">预测已应用各组件置信度扣减。历史权重保持冻结；断档不回填，停止观察可能导致该固定成本窗口无法完成。</p>
 </Panel>
 <ForwardCostEvidence data={data?.forward_cost} title="组合前向成本账户"/>
 </>;
}
