import type {SleeveExecution} from '@/lib/types/ai-factory';
import {Panel} from './components';
import {Chart} from './Charts';
import {stamp} from './data';
import './passive-execution.css';

const status:Record<string,string>={FILLED:'已成交',NO_NET_CHANGE:'账户净额未变',LEDGER_STATE:'账本状态',
 REJECTED_CAPACITY:'容量拒绝',REJECTED_MAX_QUANTITY:'数量上限拒绝',REJECTED_POST_FILL_RISK:'风险拒绝',SKIPPED_MIN_NOTIONAL:'低于最小名义金额'};

export function SleeveExecutionEvidence({data,note='费用 4 bp、滑点 5 bp 的冻结组合情景；单向账户净额只由唯一账本核算。'}:{data?:SleeveExecution;note?:string}) {
 if(!data)return null;
 const ids=data.ending.component_ids;
 const rows=data.execution.map(r=>({label:stamp(r.time,true),time:r.time,
  ...Object.fromEntries(ids.map(id=>[String(id),Number(r.positions[String(id)])]))}));
 return <Panel title="策略意图与实际持仓分配" note={note}>
 <div className="s-execution-body">
 <p>策略可以分别产生买入和卖出意图，净额为零时不会虚构两笔成交。拒单不增加持仓；账户停机和平仓反馈到各策略。</p>
 <div className="s-table-wrap" role="region" aria-label="策略持仓分配" tabIndex={0}><table className="s-table"><thead><tr><th>策略组件</th><th>固定预算</th><th>最近目标 / 总权益</th><th>实际分配 BTC</th></tr></thead><tbody>{ids.map((id,i)=><tr key={id}><td>#{id}</td><td>{data.ending.quarters[i]*25}%</td><td>{data.ending.desired_weights[String(id)]}</td><td>{data.ending.positions[String(id)]}</td></tr>)}</tbody></table></div>
 <p>账户实际净持仓：{data.ending.actual_quantity} BTC。这里没有独立的策略损益账本；内部持仓归属调整不会被计作真实场所成交。</p>
 <Chart title="各策略分配数量 · 账户反馈" kind="step" xAxis="time" rows={rows}
  series={ids.map(id=>({key:String(id),label:`组件 #${id}`}))} unit="BTC" note="只显示已经分配的数量；未成交目标不进入持仓曲线。精确数量见表格与完整材料。"/>
 <div className="s-table-wrap" role="region" aria-label="独立策略账户反馈" tabIndex={0}><table className="s-table"><thead><tr><th>时点</th><th>执行状态</th><th>实际净持仓 BTC</th><th>归属变化</th></tr></thead><tbody>{data.execution.map((r,i)=><tr key={`${r.time}/${i}`}><td>{stamp(r.time,true)}</td><td>{status[r.status]??r.status}</td><td>{r.quantity}</td><td>{r.internal_reallocation?'内部归属调整':'—'}</td></tr>)}</tbody></table></div>
 <p>完整材料共 {data.decision_count} 次决策、{data.execution_count} 次账户反馈；摘要展示最近 {data.decisions.length} 次决策及 {data.execution.length} 次反馈。</p>
 </div></Panel>;
}
