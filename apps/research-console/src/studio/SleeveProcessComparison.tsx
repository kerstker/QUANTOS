import {useId,useState} from 'react';
import type {SleeveProcess} from '@/lib/types/ai-factory';
import {Panel,Empty} from './components';
import {Chart} from './Charts';
import {fmt,stamp,statusLabel} from './data';

const names:Record<string,string>={baseline:'固定顺序',random:'随机搜索',ai:'单次 AI',committee:'三角色'};
const costs=[['4/5','手续费 4 bp · 滑点 5 bp'],['4/10','手续费 4 bp · 滑点 10 bp'],['8/5','手续费 8 bp · 滑点 5 bp']];

export function SleeveProcessComparison({data,busy,ready,action}:{data?:SleeveProcess|null;busy:boolean;ready:boolean;action:(name:string)=>void}){
 const [cost,setCost]=useState('4/5');const id=useId();
 const compare=(r:NonNullable<SleeveProcess['rows']>[number])=>r.portfolio?.comparisons?.find(c=>`${c.fee_bps}/${c.slippage_bps}`===cost);
 const groups=[...new Set(data?.rows?.map(r=>r.group)??[])];
 const rows=[0,1,2].map(fold=>({label:`第 ${fold+1} 折`,...Object.fromEntries(groups.map(group=>{
  const row=data?.rows?.find(r=>r.fold===fold&&r.group===group);
  return [group,row?compare(row)?.net_pnl_delta:undefined];
 }))}));
 return <Panel title="独立策略 · 三折持仓比较" note="沿用冻结组件和原时间分割；每个策略独立决定持仓，净目标进入同一账户。每折先内层选预算，再比较外层三种成本。"
  action={<button className="s-button" disabled={busy||!ready||data?.status==='COMPLETED_TECHNICAL_ONLY'} onClick={()=>action('sleeve_compare')}>运行独立策略三折比较</button>}>
  {!data?<Empty title="等待冻结预测比较">先完成原三折研究过程；所有已登记研究组一并纳入，缺数据和失败保留。</Empty>:<>
   <p className="f-message">零次新模型调用、零次重新拟合。复用的历史已被使用，结果保持技术诊断属性。</p>
   {data.status==='RUNNING'&&<p role="status">已完成 {data.completed_groups??0} / {data.total_groups??'—'} 组 · 第 {(data.fold??0)+1} 折 · {names[data.group??'']??data.group}</p>}
   {data.rows&&<><div className="f-toolbar"><label htmlFor={id}>独立策略三折成本情景</label><select id={id} value={cost} onChange={e=>setCost(e.target.value)}>{costs.map(([v,label])=><option key={v} value={v}>{label}</option>)}</select><span>{data.rows.length} 组已记录 · {data.failed_groups??0} 组含失败</span></div>
    <div className="s-table-wrap" role="region" aria-label="独立策略三折比较" tabIndex={0}><table className="s-table"><thead><tr><th>时间折 / 组</th><th>外层窗口</th><th>内层 / 外层 / 失败</th><th>冻结策略预算</th><th>组合净损益</th><th>单策略净损益</th><th>净增量</th><th>组合回撤 / 成交</th><th>结果与原因</th></tr></thead><tbody>{data.rows.map(r=>{
     const c=compare(r);const selected=r.portfolio?.selected;
     return <tr key={`${r.fold}-${r.group}`}><td>{r.fold+1} / {names[r.group]??r.group}</td><td>{stamp(r.test_start_ms,true)} — {stamp(r.test_end_ms,true)}</td><td>{r.inner_trials_attempted} / {r.outer_cases_attempted} / {r.failed_cases}</td><td>{selected?selected.component_ids.map((cid,i)=>`#${cid}: ${selected.quarters[i]*25}%`).join(' · '):'—'}</td><td>{fmt(c?.portfolio_net_pnl)}</td><td>{fmt(c?.baseline_net_pnl)}</td><td>{fmt(c?.net_pnl_delta)}</td><td>{c?`${fmt(c.portfolio_drawdown*100)}% / ${c.portfolio_fills}`:'—'}</td><td>{r.status==='FAILED'?`${r.stage} · ${r.reason}`:statusLabel(r.portfolio?.decision??r.status)}</td></tr>;
    })}</tbody></table></div>
    <Chart title="三折成本后持仓增量" rows={rows} series={groups.map(group=>({key:group,label:names[group]??group}))} unit="USDC" note="每点为组合相对该折内层选定单策略的净损益差；每折独立初始资金 1,500 USDC。缺失值不补零；三折不相加为连续账户收益。"/>
   </>}
  </>}
 </Panel>;
}
